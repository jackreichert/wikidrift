#!/usr/bin/env python3
"""Seed Cloudflare R2 with per-article DuckDB shards from a local Mac corpus.

Mirrors the Topic Action upload layout (``.github/workflows/topic.yml``):

    {R2_PREFIX}articles/<slug>/shard.duckdb.zst
    {R2_PREFIX}articles/<slug>/meta.json

Never uploads ``history-xml/`` or the shared monolithic ``provenance.duckdb``.

Skip heuristic (default, unless ``--force``):
  If both remote keys exist and the remote ``meta.json`` LastModified (falling back to
  ``meta.updated_at``) is >= the local ``provenance.duckdb`` mtime, the slug is skipped.
  Otherwise the slug is (re)uploaded.

Locked DuckDB files (e.g. an in-progress Zionism pull) are skipped with a clear message;
the loop continues.

Examples::

    uv run python tools/seed_r2_shards.py --dry-run
    uv run python tools/seed_r2_shards.py --topics Brontosaurus Chess
    uv run python tools/seed_r2_shards.py --force
    uv run python tools/seed_r2_shards.py --all
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import duckdb

from wikidrift import config
from wikidrift.config.parsing import slugify
from wikidrift.corpus import Corpus
from wikidrift import shards

DEFAULT_ARTICLES_DIR = Path(".planning/spikes/data/articles")
_SKIP_DIR_NAMES = {"_shared"}
_LOCK_HINTS = (
    "lock",
    "conflicting",
    "could not set lock",
    "resource busy",
    "database is locked",
    "io error",
)


def _load_nearby_dotenv(articles_dir: Path) -> None:
    """Load repo-root ``.env`` without printing values (cwd + walk from articles_dir)."""
    candidates = [Path.cwd() / ".env"]
    here = articles_dir.resolve()
    for parent in [here, *here.parents]:
        candidates.append(parent / ".env")
        if (parent / "pyproject.toml").is_file():
            break
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        config._load_dotenv(path)


def _normalize_prefix(prefix: str | None) -> str:
    value = prefix or ""
    if value and not value.endswith("/"):
        value = f"{value}/"
    return value


def _require_r2_env(*, dry_run: bool) -> dict[str, str]:
    """Return R2 settings; refuse to print secret values."""
    account = os.environ.get("R2_ACCOUNT_ID", "").strip()
    access = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    bucket = os.environ.get("R2_BUCKET", "").strip()
    prefix = _normalize_prefix(os.environ.get("R2_PREFIX"))
    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", account),
            ("R2_ACCESS_KEY_ID", access),
            ("R2_SECRET_ACCESS_KEY", secret),
            ("R2_BUCKET", bucket),
        )
        if not value
    ]
    if missing and not dry_run:
        raise SystemExit(
            "missing required R2 env vars (set in repo .env or the environment): "
            + ", ".join(missing)
        )
    return {
        "account_id": account,
        "access_key_id": access,
        "secret_access_key": secret,
        "bucket": bucket,
        "prefix": prefix,
        "endpoint": f"https://{account}.r2.cloudflarestorage.com" if account else "",
    }


def _is_lock_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in text for hint in _LOCK_HINTS)


def _article_title_for_slug(shard_dir: Path, slug: str) -> str:
    manifest = shard_dir / "migration-manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            article = data.get("article")
            if isinstance(article, str) and article.strip():
                return article.strip()
        except (OSError, json.JSONDecodeError):
            pass
    identity = shard_dir / "article-identity.json"
    if identity.is_file():
        try:
            data = json.loads(identity.read_text(encoding="utf-8"))
            for key in ("canonical_title", "title", "article"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except (OSError, json.JSONDecodeError):
            pass
    db = shard_dir / "provenance.duckdb"
    if db.is_file():
        try:
            con = duckdb.connect(str(db), read_only=True)
            try:
                row = con.execute(
                    "SELECT article FROM revisions WHERE article IS NOT NULL LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
            finally:
                con.close()
        except duckdb.Error:
            pass
    return slug.replace("_", " ")


def _discover_slugs(articles_dir: Path) -> list[str]:
    if not articles_dir.is_dir():
        return []
    slugs = []
    for path in sorted(articles_dir.iterdir()):
        if not path.is_dir() or path.name in _SKIP_DIR_NAMES or path.name.startswith("."):
            continue
        slugs.append(path.name)
    return slugs


def _resolve_selection(
    articles_dir: Path,
    topics: list[str] | None,
    all_articles: bool,
) -> list[tuple[str, str]]:
    """Return ``(slug, title)`` pairs to process."""
    if topics:
        pairs = []
        for topic in topics:
            title = topic.strip()
            if not title:
                continue
            pairs.append((slugify(title), title))
        return pairs
    # Default and --all: every article directory under articles_dir.
    _ = all_articles  # flag documented for operators; discovery is the same set
    pairs = []
    for slug in _discover_slugs(articles_dir):
        pairs.append((slug, _article_title_for_slug(articles_dir / slug, slug)))
    return pairs


def _ensure_local_db(
    articles_dir: Path,
    slug: str,
    title: str,
) -> Path:
    """Ensure ``articles/<slug>/provenance.duckdb``, rematerializing from parent when needed."""
    shard_dir = articles_dir / slug
    target = shard_dir / "provenance.duckdb"
    if target.is_file():
        return target
    canonical = shards.resolve_canonical_database(articles_dir)
    if canonical is None:
        raise FileNotFoundError(
            f"article shard database missing: {target}\n"
            f"No canonical corpus at {articles_dir.parent / 'provenance.duckdb'} "
            "to materialize from."
        )
    print(f"  rematerialize {title!r} from {canonical}", flush=True)
    return shards.ensure_article_database(canonical, shard_dir, title)


def _build_meta(db: Path, title: str, slug: str, source: str = "mac-seed") -> dict:
    horizon = None
    last_revid = None
    try:
        con = duckdb.connect(str(db), read_only=True)
        try:
            row = Corpus(con).latest_snapshot(title)
            if row:
                horizon = {"snapshot_date": row[0], "snapshot_revid": int(row[1])}
                last_revid = int(row[1])
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — meta is best-effort, like topic.yml
        if _is_lock_error(exc):
            raise
        print(f"  meta horizon unavailable: {type(exc).__name__}: {exc}", flush=True)
    return {
        "title": title,
        "slug": slug,
        "last_revid": last_revid,
        "horizon": horizon,
        "updated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "artifact": "shard.duckdb.zst",
    }


def _aws_env(r2: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = r2["access_key_id"]
    env["AWS_SECRET_ACCESS_KEY"] = r2["secret_access_key"]
    env["AWS_DEFAULT_REGION"] = env.get("AWS_DEFAULT_REGION") or "auto"
    # Avoid accidental profile credentials overriding R2 keys.
    env.pop("AWS_PROFILE", None)
    return env


def _aws_s3(
    args: list[str],
    r2: dict[str, str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = ["aws", "s3", *args, "--endpoint-url", r2["endpoint"], "--only-show-errors"]
    return subprocess.run(
        cmd,
        env=_aws_env(r2),
        check=check,
        text=True,
        capture_output=True,
    )


def _head_last_modified(r2: dict[str, str], key: str) -> dt.datetime | None:
    """Return object LastModified in UTC, or None if missing / unreadable."""
    cmd = [
        "aws",
        "s3api",
        "head-object",
        "--bucket",
        r2["bucket"],
        "--key",
        key,
        "--endpoint-url",
        r2["endpoint"],
        "--output",
        "json",
    ]
    result = subprocess.run(
        cmd,
        env=_aws_env(r2),
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    raw = payload.get("LastModified")
    if not raw:
        return None
    # aws returns e.g. "2026-10-21T18:57:54+00:00"
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _remote_skip_timestamp(r2: dict[str, str], final_prefix: str) -> dt.datetime | None:
    """Newest usable remote timestamp for skip comparisons, or None if incomplete."""
    meta_key = f"{final_prefix}/meta.json"
    shard_key = f"{final_prefix}/shard.duckdb.zst"
    meta_lm = _head_last_modified(r2, meta_key)
    shard_lm = _head_last_modified(r2, shard_key)
    if meta_lm is None or shard_lm is None:
        return None
    # Prefer meta LastModified; fall back to fetching meta body updated_at only if needed.
    return max(meta_lm, shard_lm)


def _should_skip_remote(
    r2: dict[str, str],
    final_prefix: str,
    local_db: Path,
    *,
    force: bool,
) -> bool:
    if force:
        return False
    remote_ts = _remote_skip_timestamp(r2, final_prefix)
    if remote_ts is None:
        return False
    local_mtime = dt.datetime.fromtimestamp(local_db.stat().st_mtime, tz=dt.timezone.utc)
    return local_mtime <= remote_ts


def _compress_shard(db: Path, dest_zst: Path) -> None:
    zstd = shutil.which("zstd")
    if not zstd:
        raise RuntimeError("zstd not found on PATH (required to compress shard.duckdb.zst)")
    subprocess.run(
        [zstd, "-f", "-o", str(dest_zst), str(db)],
        check=True,
        capture_output=True,
        text=True,
    )


def _upload_pair(
    r2: dict[str, str],
    final_prefix: str,
    zst_path: Path,
    meta_path: Path,
) -> None:
    staging = f"{final_prefix}/_upload_{uuid.uuid4().hex[:12]}"
    try:
        _aws_s3(["cp", str(zst_path), f"s3://{r2['bucket']}/{staging}/shard.duckdb.zst"], r2)
        _aws_s3(["cp", str(meta_path), f"s3://{r2['bucket']}/{staging}/meta.json"], r2)
        _aws_s3(
            [
                "cp",
                f"s3://{r2['bucket']}/{staging}/shard.duckdb.zst",
                f"s3://{r2['bucket']}/{final_prefix}/shard.duckdb.zst",
            ],
            r2,
        )
        _aws_s3(
            [
                "cp",
                f"s3://{r2['bucket']}/{staging}/meta.json",
                f"s3://{r2['bucket']}/{final_prefix}/meta.json",
            ],
            r2,
        )
    finally:
        subprocess.run(
            [
                "aws",
                "s3",
                "rm",
                f"s3://{r2['bucket']}/{staging}/",
                "--recursive",
                "--endpoint-url",
                r2["endpoint"],
                "--only-show-errors",
            ],
            env=_aws_env(r2),
            check=False,
            capture_output=True,
            text=True,
        )


def _process_one(
    articles_dir: Path,
    slug: str,
    title: str,
    r2: dict[str, str],
    *,
    dry_run: bool,
    force: bool,
) -> str:
    """Return status: uploaded | would-upload | skipped | locked | failed."""
    final_prefix = f"{r2['prefix']}articles/{slug}"
    shard_key = f"{final_prefix}/shard.duckdb.zst"
    meta_key = f"{final_prefix}/meta.json"
    try:
        db = _ensure_local_db(articles_dir, slug, title)
    except Exception as exc:  # noqa: BLE001
        if _is_lock_error(exc):
            print(f"  SKIP locked DB while ensuring shard: {exc}", flush=True)
            return "locked"
        print(f"  FAIL ensure DB: {type(exc).__name__}: {exc}", flush=True)
        return "failed"

    try:
        # Probe readability early (captures writer locks on the shard).
        probe = duckdb.connect(str(db), read_only=True)
        probe.close()
    except Exception as exc:  # noqa: BLE001
        if _is_lock_error(exc):
            print(f"  SKIP locked shard DB ({db}): {exc}", flush=True)
            return "locked"
        print(f"  FAIL open shard: {type(exc).__name__}: {exc}", flush=True)
        return "failed"

    if not dry_run and r2["bucket"]:
        try:
            if _should_skip_remote(r2, final_prefix, db, force=force):
                print(
                    f"  SKIP remote up-to-date ({meta_key} / {shard_key})",
                    flush=True,
                )
                return "skipped"
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN remote head failed; will upload: {type(exc).__name__}: {exc}", flush=True)

    try:
        meta = _build_meta(db, title, slug)
    except Exception as exc:  # noqa: BLE001
        if _is_lock_error(exc):
            print(f"  SKIP locked DB while building meta: {exc}", flush=True)
            return "locked"
        print(f"  FAIL meta: {type(exc).__name__}: {exc}", flush=True)
        return "failed"

    if dry_run:
        print(
            f"  would upload s3://{r2['bucket'] or '<bucket>'}/{shard_key} "
            f"+ meta.json (local db {db.stat().st_size} bytes)",
            flush=True,
        )
        return "would-upload"

    with tempfile.TemporaryDirectory(prefix="wikidrift-r2-seed-") as tmp:
        stage = Path(tmp)
        zst_path = stage / "shard.duckdb.zst"
        meta_path = stage / "meta.json"
        try:
            _compress_shard(db, zst_path)
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            _upload_pair(r2, final_prefix, zst_path, meta_path)
        except Exception as exc:  # noqa: BLE001
            if _is_lock_error(exc):
                print(f"  SKIP locked during compress/upload: {exc}", flush=True)
                return "locked"
            print(f"  FAIL upload: {type(exc).__name__}: {exc}", flush=True)
            return "failed"

    print(f"  uploaded {shard_key} + {meta_key}", flush=True)
    return "uploaded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed Cloudflare R2 with local article shard DuckDBs (Action key layout).",
    )
    parser.add_argument(
        "--articles-dir",
        type=Path,
        default=DEFAULT_ARTICLES_DIR,
        help=f"Article shard root (default: {DEFAULT_ARTICLES_DIR})",
    )
    parser.add_argument(
        "--topics",
        nargs="+",
        metavar="TITLE",
        help="Wikipedia titles to seed (slugified). Default: all article dirs.",
    )
    parser.add_argument(
        "--all",
        dest="all_articles",
        action="store_true",
        help="Seed every article directory under --articles-dir (default when --topics omitted).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be uploaded; do not compress or call R2.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-upload even when remote keys look up-to-date.",
    )
    args = parser.parse_args(argv)

    articles_dir = args.articles_dir
    _load_nearby_dotenv(articles_dir)
    # Importing wikidrift.config already loaded package-root .env; nearby pass covers main checkout.

    if args.topics and args.all_articles:
        parser.error("use only one of --topics or --all")

    r2 = _require_r2_env(dry_run=args.dry_run)
    selection = _resolve_selection(articles_dir, args.topics, args.all_articles)
    if not selection:
        print(f"no articles found under {articles_dir}", flush=True)
        return 1

    print(
        f"seeding {len(selection)} article(s) from {articles_dir} "
        f"→ s3://{r2['bucket'] or '<bucket>'}/{r2['prefix']}articles/<slug>/ "
        f"{'(dry-run)' if args.dry_run else ''}".rstrip(),
        flush=True,
    )
    print(
        "skip heuristic: remote meta+shard present and remote LastModified >= local "
        "provenance.duckdb mtime (override with --force)",
        flush=True,
    )

    counts = {
        "uploaded": 0,
        "would-upload": 0,
        "skipped": 0,
        "locked": 0,
        "failed": 0,
    }
    for index, (slug, title) in enumerate(selection, 1):
        print(f"[{index}/{len(selection)}] {title} ({slug})", flush=True)
        status = _process_one(
            articles_dir,
            slug,
            title,
            r2,
            dry_run=args.dry_run,
            force=args.force,
        )
        counts[status] = counts.get(status, 0) + 1

    print(
        "summary: "
        + ", ".join(f"{key}={value}" for key, value in counts.items() if value),
        flush=True,
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

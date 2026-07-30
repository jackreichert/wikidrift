"""Copy the canonical corpus into independently writable per-article shards."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import uuid

import duckdb

from . import provenance
from .config.parsing import slugify


_SHARED_SLUG = "_shared"
_EXPECTED_INDEXES = {
    "ix_rsnap_art_rev",
    "ix_rsnap_art_tok",
    "ix_rev_art_id",
    "ix_revsize_art_id",
    "ix_tokens_art_tok",
}


def migrate_all(data_dir: pathlib.Path, articles_dir: pathlib.Path | None = None) -> dict:
    """Copy every attributable row and artifact into an article-owned shard.

    The canonical inputs remain untouched. Each destination database is rebuilt in a temporary
    file, verified against source row counts, and atomically replaced only after verification.
    """
    source_dir = pathlib.Path(data_dir)
    source_db = source_dir / "provenance.duckdb"
    destination = pathlib.Path(articles_dir) if articles_dir else source_dir / "articles"
    if not source_db.is_file():
        raise FileNotFoundError(f"canonical database not found: {source_db}")

    source_guard = duckdb.connect(str(source_db), read_only=True)
    try:
        table_names, database_articles = _database_inventory(source_guard)
        article_by_slug = _article_registry(source_dir, database_articles)
        destination.mkdir(parents=True, exist_ok=True)

        manifests = []
        articles = sorted(article_by_slug.items())
        for index, (slug, article) in enumerate(articles, 1):
            print(f"  shard [{index}/{len(articles)}] {article}", flush=True)
            shard_dir = destination / slug
            shard_dir.mkdir(parents=True, exist_ok=True)
            table_counts = _copy_article_database(source_db, shard_dir / "provenance.duckdb",
                                                  table_names, article)
            artifacts = _copy_article_artifacts(source_dir, shard_dir, slug)
            manifest = {
                "article": article,
                "slug": slug,
                "tables": table_counts,
                "artifacts": artifacts,
            }
            _write_json_atomic(shard_dir / "migration-manifest.json", manifest)
            manifests.append(manifest)

        shared_artifacts = _copy_shared_artifacts(source_dir, destination / _SHARED_SLUG,
                                                  set(article_by_slug))
    finally:
        source_guard.close()
    report = {
        "article_count": len(manifests),
        "articles": manifests,
        "shared_artifacts": shared_artifacts,
    }
    _write_json_atomic(destination / "migration-report.json", report)
    return report


def _database_inventory(con) -> tuple[list[str], list[str]]:
    tables = [row[0] for row in con.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """).fetchall()]
    article_tables = []
    articles = set()
    for table in tables:
        columns = {row[1] for row in con.execute(f"PRAGMA table_info({_quote_literal(table)})").fetchall()}
        if "article" not in columns:
            raise ValueError(f"cannot attribute table without an article column: {table}")
        article_tables.append(table)
        query = f"SELECT DISTINCT article FROM {_quote_identifier(table)} WHERE article IS NOT NULL"
        articles.update(row[0] for row in con.execute(query).fetchall())
    return article_tables, sorted(articles)


def _article_registry(source_dir: pathlib.Path, database_articles: list[str]) -> dict[str, str]:
    article_by_slug = {}
    for article in database_articles:
        slug = slugify(article)
        previous = article_by_slug.setdefault(slug, article)
        if previous != article:
            raise ValueError(f"article slug collision: {previous!r} and {article!r} both map to {slug!r}")

    findings = source_dir / "findings"
    if findings.is_dir():
        for artifact in findings.glob("*.json"):
            parts = artifact.name.split(".")
            if len(parts) >= 3:
                article_by_slug.setdefault(parts[0], parts[0].replace("_", " "))

    for directory, pattern in ((source_dir / "mscore", "*.json"),
                               (source_dir / "history-xml", "*.xml")):
        if directory.is_dir():
            for artifact in directory.glob(pattern):
                article_by_slug.setdefault(artifact.stem, artifact.stem.replace("_", " "))
    return article_by_slug


def _copy_article_database(source_db: pathlib.Path, target_db: pathlib.Path, tables: list[str],
                           article: str) -> dict[str, int]:
    temporary = target_db.with_name(f".{target_db.name}.{uuid.uuid4().hex}.tmp")
    con = duckdb.connect(str(temporary))
    try:
        con.execute(f"ATTACH {_quote_literal(str(source_db))} AS canonical (READ_ONLY)")
        expected = {}
        for table in tables:
            identifier = _quote_identifier(table)
            expected[table] = con.execute(
                f"SELECT count(*) FROM canonical.{identifier} WHERE article = ?", [article]
            ).fetchone()[0]
            con.execute(
                f"CREATE TABLE {identifier} AS SELECT * FROM canonical.{identifier} WHERE article = ?",
                [article],
            )
        con.execute("DETACH canonical")
        provenance.ensure_indexes(con)
        indexes = {row[0] for row in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
        missing_indexes = _EXPECTED_INDEXES - indexes
        if missing_indexes:
            missing = ", ".join(sorted(missing_indexes))
            raise RuntimeError(f"index verification failed for {article!r}; missing: {missing}")
        actual = {
            table: con.execute(f"SELECT count(*) FROM {_quote_identifier(table)}").fetchone()[0]
            for table in tables
        }
        if actual != expected:
            raise RuntimeError(f"row-count verification failed for {article!r}: {actual} != {expected}")
    except Exception:
        con.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        con.close()
        os.replace(temporary, target_db)
        return expected


def _copy_article_artifacts(source_dir: pathlib.Path, shard_dir: pathlib.Path, slug: str) -> list[str]:
    sources = []
    findings = source_dir / "findings"
    if findings.is_dir():
        sources.extend(path for path in findings.glob(f"{slug}.*") if path.is_file())
    for directory, suffix in (("mscore", ".json"), ("history-xml", ".xml")):
        artifact = source_dir / directory / f"{slug}{suffix}"
        if artifact.is_file():
            sources.append(artifact)

    logs = source_dir / "logs"
    if logs.is_dir():
        sources.extend(path for path in logs.rglob(f"{slug}.*") if path.is_file())
    return _copy_verified(source_dir, shard_dir, sources)


def _copy_shared_artifacts(source_dir: pathlib.Path, shared_dir: pathlib.Path,
                           article_slugs: set[str]) -> list[str]:
    sources = []
    findings = source_dir / "findings"
    if findings.is_dir():
        sources.extend(
            path for path in findings.iterdir()
            if path.is_file() and not any(path.name.startswith(f"{slug}.") for slug in article_slugs)
        )
    logs = source_dir / "logs"
    if logs.is_dir():
        sources.extend(
            path for path in logs.rglob("*")
            if path.is_file() and not any(path.name.startswith(f"{slug}.") for slug in article_slugs)
        )
    return _copy_verified(source_dir, shared_dir, sources)


def _copy_verified(source_dir: pathlib.Path, shard_dir: pathlib.Path,
                   sources: list[pathlib.Path]) -> list[str]:
    copied = []
    for source in sorted(set(sources)):
        relative = source.relative_to(source_dir)
        target = shard_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if _sha256(source) != _sha256(target):
            raise RuntimeError(f"artifact checksum verification failed: {relative}")
        copied.append(relative.as_posix())
    return copied


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
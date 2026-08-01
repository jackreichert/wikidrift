"""Provenance layer — DuckDB schema + token/revision fetching from public APIs.

Two data sources, per the design (§3):
  - hosted WikiWho API — per-token authorship + in/out lifecycle for a given revision.
  - MediaWiki Action API — the rev_id -> (timestamp, user) timeline and per-rev byte size.

Everything the L1 drift engine (drift.py) consumes lives in these tables:
  revisions(article, rev_id, ts, user)          -- the timeline
  rev_size(article, rev_id, size)               -- per-rev byte size (vandalism-robust snapshotting)
  rsnap(article, snap_date, snap_rev, token_id, o_rev_id)  -- persistent snapshots (the PWR corpus)
  tokens/articles                               -- 001a's latest-revision provenance (kept for compat)

WikiWho hosted flakiness is handled with fail-fast timeouts + retry/backoff (learned in the base-rate
run). For batch/scale and coverage gaps, the local wikiwho_rs-on-dumps backend feeds the same rsnap
schema via `ingest.py` (both share `snapshot_picks` for identical revision selection).
"""
import json
import time
import urllib.parse
import statistics
import datetime as dt
from collections import OrderedDict
from dataclasses import dataclass

from . import config

_S = config.session()
# (rev_id, io) -> tokens, process-local LRU cache. Bounded so corpus-scale callers (bootstrap /
# l4.discover looping over many articles in one process) can't grow it without limit (Release It!
# Steady State) — a whole article's analysis needs far fewer than the cap.
_TOK_CAP = 4096
_tok = OrderedDict()

SNAPSHOT_INTEGRITY_POLICY = "snapshot-integrity-v1"
MIN_NEIGHBOR_TOKEN_RATIO = 0.5
MIN_NEIGHBOR_BYTE_RATIO = 0.8
STABLE_ENDPOINT_POLICY = "stable-endpoint-v1"
MIN_ENDPOINT_SURVIVAL_SECONDS = 48 * 60 * 60


def assess_snapshot_integrity(
        token_rows, unique_tokens, revision_bytes,
        previous_token_rows=None, next_token_rows=None,
        previous_revision_bytes=None, next_revision_bytes=None):
    """Classify one snapshot from token, revision-size, and neighboring evidence."""
    metrics = {
        "token_rows": token_rows,
        "unique_tokens": unique_tokens,
        "revision_bytes": revision_bytes,
        "duplicate_rate": 1.0 - (unique_tokens / token_rows) if token_rows else None,
        "previous_token_ratio": (
            token_rows / previous_token_rows if previous_token_rows else None
        ),
        "next_token_ratio": next_token_rows and token_rows / next_token_rows,
        "previous_byte_ratio": (
            revision_bytes / previous_revision_bytes if previous_revision_bytes else None
        ),
        "next_byte_ratio": (
            revision_bytes / next_revision_bytes if next_revision_bytes else None
        ),
    }
    if token_rows <= 0 or unique_tokens <= 0:
        return {
            "status": "quarantined",
            "reason": "snapshot contains no usable token membership",
            "metrics": metrics,
            "policy_version": SNAPSHOT_INTEGRITY_POLICY,
        }
    if unique_tokens != token_rows:
        return {
            "status": "quarantined",
            "reason": "snapshot contains duplicate token membership",
            "metrics": metrics,
            "policy_version": SNAPSHOT_INTEGRITY_POLICY,
        }

    token_ratios = [
        ratio for ratio in (
            metrics["previous_token_ratio"], metrics["next_token_ratio"]
        ) if ratio is not None
    ]
    byte_ratios = [
        ratio for ratio in (
            metrics["previous_byte_ratio"], metrics["next_byte_ratio"]
        ) if ratio is not None
    ]
    severe_membership_loss = (
        len(token_ratios) == 2
        and max(token_ratios) < MIN_NEIGHBOR_TOKEN_RATIO
    )
    stable_revision_size = (
        len(byte_ratios) == 2
        and min(byte_ratios) >= MIN_NEIGHBOR_BYTE_RATIO
    )
    if severe_membership_loss and stable_revision_size:
        return {
            "status": "suspect",
            "reason": "token membership is inconsistent with revision size and adjacent snapshots",
            "metrics": metrics,
            "policy_version": SNAPSHOT_INTEGRITY_POLICY,
        }
    return {
        "status": "complete",
        "reason": None,
        "metrics": metrics,
        "policy_version": SNAPSHOT_INTEGRITY_POLICY,
    }


def select_stable_endpoint(snapshots, observed_at, minimum_survival_seconds=None):
    """Select the newest age-qualified snapshot and explain excluded newer candidates."""
    minimum_survival = (
        MIN_ENDPOINT_SURVIVAL_SECONDS
        if minimum_survival_seconds is None
        else minimum_survival_seconds
    )
    ordered = sorted(snapshots, key=lambda snapshot: (snapshot[0], snapshot[1]))
    latest_seen = ordered[-1][1] if ordered else None
    excluded = []
    selected = None
    for snap_date, revision_id, timestamp in reversed(ordered):
        if not timestamp:
            excluded.append({
                "revision_id": revision_id,
                "reason": "missing_revision_timestamp",
                "evidence_revid": None,
            })
            continue
        try:
            revision_time = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if revision_time.tzinfo is None:
                revision_time = revision_time.replace(tzinfo=dt.timezone.utc)
        except (AttributeError, TypeError, ValueError):
            excluded.append({
                "revision_id": revision_id,
                "reason": "invalid_revision_timestamp",
                "evidence_revid": None,
            })
            continue
        survival_seconds = max(0, int((observed_at - revision_time).total_seconds()))
        if survival_seconds < minimum_survival:
            excluded.append({
                "revision_id": revision_id,
                "reason": "minimum_survival_not_met",
                "evidence_revid": None,
            })
            continue
        selected = {
            "snapshot_date": snap_date,
            "revision_id": revision_id,
            "timestamp": timestamp,
            "survival_seconds": survival_seconds,
        }
        break
    return {
        "mode": "current_stable",
        "latest_seen_revid": latest_seen,
        "selected_revid": selected["revision_id"] if selected else None,
        "selected_snapshot_date": selected["snapshot_date"] if selected else None,
        "selected_timestamp": selected["timestamp"] if selected else None,
        "survival_seconds": selected["survival_seconds"] if selected else None,
        "confirmed_by_revid": None,
        "status": "stable" if selected else "unstable",
        "excluded_revisions": excluded,
        "policy_version": STABLE_ENDPOINT_POLICY,
    }


@dataclass(frozen=True)
class ResolvedArticle:
    """Canonical MediaWiki identity for one requested article title."""

    requested_title: str
    canonical_title: str
    page_id: int


def resolve_article_title(article, session=None):
    """Resolve redirects before history ingestion or article-shard selection."""
    requested = (article or "").strip()
    if not requested:
        raise ValueError("article title is required")
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "redirects": 1,
        "titles": requested,
        "prop": "info",
    }
    payload = config.get_json_retrying(session or _S, config.ACTION, params=params)
    pages = payload.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or "pageid" not in pages[0]:
        raise ValueError(f"article not found: {requested}")
    page = pages[0]
    return ResolvedArticle(
        requested_title=requested,
        canonical_title=page["title"],
        page_id=int(page["pageid"]),
    )


def _tok_put(key, val):
    """Insert into the bounded token cache, evicting the least-recently-used entry past the cap."""
    _tok[key] = val
    if len(_tok) > _TOK_CAP:
        _tok.popitem(last=False)
    return val


# --- schema -----------------------------------------------------------------
def ensure_schema(con):
    con.execute("CREATE TABLE IF NOT EXISTS articles(article TEXT, page_id BIGINT, latest_rev BIGINT, latest_time TEXT, n_tokens BIGINT)")
    con.execute("""CREATE TABLE IF NOT EXISTS article_identity(
        requested_title TEXT, canonical_title TEXT, page_id BIGINT, resolved_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS article_source_state(
        article TEXT PRIMARY KEY, source_status TEXT, source_checked_at TEXT,
        source_latest_revid BIGINT, expected_snapshots INT, loaded_snapshots INT, reason TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS revisions(article TEXT, rev_id BIGINT, ts TEXT, user TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS revision_metadata(
        article TEXT, rev_id BIGINT, parent_id BIGINT, sha1 TEXT, comment TEXT,
        tags TEXT, minor BOOLEAN, user_hidden BOOLEAN, retrieved_at TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS tokens(article TEXT, token_id BIGINT, str TEXT, editor TEXT, o_rev_id BIGINT, n_in INT, n_out INT)")
    con.execute("CREATE TABLE IF NOT EXISTS rev_size(article TEXT, rev_id BIGINT, size BIGINT)")
    con.execute("CREATE TABLE IF NOT EXISTS rsnap(article TEXT, snap_date TEXT, snap_rev BIGINT, token_id BIGINT, o_rev_id BIGINT)")
    con.execute("""CREATE TABLE IF NOT EXISTS snapshot_integrity(
        article TEXT, snap_date TEXT, snap_rev BIGINT, status TEXT,
        token_rows BIGINT, unique_tokens BIGINT, revision_bytes BIGINT,
        duplicate_rate DOUBLE, previous_token_ratio DOUBLE, next_token_ratio DOUBLE,
        previous_byte_ratio DOUBLE, next_byte_ratio DOUBLE,
        reason TEXT, policy_version TEXT, checked_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS endpoint_receipts(
        article TEXT, mode TEXT, latest_seen_revid BIGINT, selected_revid BIGINT,
        selected_snapshot_date TEXT, selected_timestamp TEXT, survival_seconds BIGINT,
        confirmed_by_revid BIGINT, status TEXT, excluded_revisions TEXT,
        policy_version TEXT, checked_at TEXT)""")


def refresh_snapshot_integrity(con, article):
    """Recompute and persist integrity receipts for all loaded snapshots of one article."""
    ensure_schema(con)
    receipts = assess_article_snapshot_integrity(con, article)
    con.execute("DELETE FROM snapshot_integrity WHERE article=?", [article])
    if receipts:
        con.executemany("INSERT INTO snapshot_integrity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [(
            receipt["article"], receipt["snapshot_date"], receipt["snapshot_revid"],
            receipt["status"], receipt["token_rows"], receipt["unique_tokens"],
            receipt["revision_bytes"], receipt["duplicate_rate"],
            receipt["previous_token_ratio"], receipt["next_token_ratio"],
            receipt["previous_byte_ratio"], receipt["next_byte_ratio"], receipt["reason"],
            receipt["policy_version"], receipt["checked_at"],
        ) for receipt in receipts])
    return receipts


def assess_article_snapshot_integrity(con, article):
    """Assess all loaded snapshots of one article without mutating the database."""
    rows = con.execute("""
        SELECT s.snap_date, s.snap_rev, count(*) AS token_rows,
               count(DISTINCT s.token_id) AS unique_tokens, max(z.size) AS revision_bytes
        FROM rsnap s
        LEFT JOIN rev_size z ON z.article=s.article AND z.rev_id=s.snap_rev
        WHERE s.article=?
        GROUP BY s.snap_date, s.snap_rev
        ORDER BY s.snap_date, s.snap_rev
    """, [article]).fetchall()
    checked_at = dt.datetime.now(dt.timezone.utc).isoformat()
    receipts = []
    for index, (snap_date, snap_rev, token_rows, unique_tokens, revision_bytes) in enumerate(rows):
        previous = rows[index - 1] if index > 0 else None
        following = rows[index + 1] if index + 1 < len(rows) else None
        result = assess_snapshot_integrity(
            token_rows=token_rows,
            unique_tokens=unique_tokens,
            revision_bytes=revision_bytes or 0,
            previous_token_rows=previous[2] if previous else None,
            next_token_rows=following[2] if following else None,
            previous_revision_bytes=previous[4] if previous else None,
            next_revision_bytes=following[4] if following else None,
        )
        metrics = result["metrics"]
        receipts.append({
            "article": article,
            "snapshot_date": snap_date,
            "snapshot_revid": snap_rev,
            "status": result["status"],
            "token_rows": token_rows,
            "unique_tokens": unique_tokens,
            "revision_bytes": revision_bytes,
            "duplicate_rate": metrics["duplicate_rate"],
            "previous_token_ratio": metrics["previous_token_ratio"],
            "next_token_ratio": metrics["next_token_ratio"],
            "previous_byte_ratio": metrics["previous_byte_ratio"],
            "next_byte_ratio": metrics["next_byte_ratio"],
            "reason": result["reason"],
            "policy_version": result["policy_version"],
            "checked_at": checked_at,
        })
    return receipts


def load_snapshot_integrity(con, article):
    """Return persisted integrity receipts for one article in snapshot order."""
    ensure_schema(con)
    columns = (
        "article", "snapshot_date", "snapshot_revid", "status", "token_rows",
        "unique_tokens", "revision_bytes", "duplicate_rate", "previous_token_ratio",
        "next_token_ratio", "previous_byte_ratio", "next_byte_ratio", "reason",
        "policy_version", "checked_at",
    )
    rows = con.execute("""SELECT article, snap_date, snap_rev, status, token_rows,
        unique_tokens, revision_bytes, duplicate_rate, previous_token_ratio, next_token_ratio,
        previous_byte_ratio, next_byte_ratio, reason, policy_version, checked_at
        FROM snapshot_integrity WHERE article=? ORDER BY snap_date, snap_rev""", [article]).fetchall()
    return [dict(zip(columns, row)) for row in rows]


def audit_snapshot_integrity(con, articles=None, persist=False):
    """Report corpus integrity, optionally persisting receipts as an explicit backfill."""
    if persist:
        ensure_schema(con)
    targets = articles or [row[0] for row in con.execute(
        "SELECT DISTINCT article FROM rsnap ORDER BY article"
    ).fetchall()]
    article_reports = []
    totals = {"complete": 0, "suspect": 0, "quarantined": 0}
    for article in targets:
        receipts = (
            refresh_snapshot_integrity(con, article)
            if persist else assess_article_snapshot_integrity(con, article)
        )
        counts = {status: 0 for status in totals}
        for receipt in receipts:
            counts[receipt["status"]] += 1
            totals[receipt["status"]] += 1
        if persist and counts["quarantined"]:
            current = load_source_state(con, article) or {}
            record_source_state(
                con,
                article,
                source_status="partial",
                expected_snapshots=current.get("expected_snapshots") or len(receipts),
                loaded_snapshots=current.get("loaded_snapshots") or len(receipts),
                reason=f"{counts['quarantined']} snapshot(s) failed integrity checks",
            )
        article_reports.append({
            "article": article,
            "snapshots": len(receipts),
            "counts": counts,
            "status": (
                "quarantined" if counts["quarantined"]
                else "suspect" if counts["suspect"]
                else "complete"
            ),
        })
    return {
        "policy_version": SNAPSHOT_INTEGRITY_POLICY,
        "article_count": len(article_reports),
        "totals": totals,
        "articles": article_reports,
    }


def refresh_stable_endpoint(con, article, observed_at=None):
    """Recompute and persist the current stable endpoint receipt for one article."""
    ensure_schema(con)
    receipt, checked_at = assess_stable_endpoint(con, article, observed_at=observed_at)
    con.execute(
        "DELETE FROM endpoint_receipts WHERE article=? AND mode='current_stable'", [article]
    )
    con.execute("INSERT INTO endpoint_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
        article, receipt["mode"], receipt["latest_seen_revid"], receipt["selected_revid"],
        receipt["selected_snapshot_date"], receipt["selected_timestamp"],
        receipt["survival_seconds"], receipt["confirmed_by_revid"], receipt["status"],
        json.dumps(receipt["excluded_revisions"], sort_keys=True), receipt["policy_version"],
        checked_at.isoformat(),
    ))
    return receipt


def assess_stable_endpoint(con, article, observed_at=None):
    """Assess the current stable endpoint without mutating the database."""
    has_integrity = bool(con.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='main' AND table_name='snapshot_integrity'""").fetchone()[0])
    integrity_clause = """NOT EXISTS (
            SELECT 1 FROM snapshot_integrity integrity
            WHERE integrity.article=s.article AND integrity.snap_rev=s.snap_rev
              AND integrity.status='quarantined')""" if has_integrity else "TRUE"
    snapshots = con.execute("""
        SELECT DISTINCT s.snap_date, s.snap_rev, r.ts
        FROM rsnap s
        LEFT JOIN revisions r ON r.article=s.article AND r.rev_id=s.snap_rev
        WHERE s.article=? AND {integrity_clause}
        ORDER BY s.snap_date, s.snap_rev
    """.format(integrity_clause=integrity_clause), [article]).fetchall()
    checked_at = observed_at or dt.datetime.now(dt.timezone.utc)
    receipt = select_stable_endpoint(snapshots, checked_at)
    return receipt, checked_at


def load_stable_endpoint(con, article):
    """Return the persisted current stable endpoint receipt, or None."""
    ensure_schema(con)
    row = con.execute("""SELECT mode, latest_seen_revid, selected_revid,
        selected_snapshot_date, selected_timestamp, survival_seconds, confirmed_by_revid,
        status, excluded_revisions, policy_version, checked_at
        FROM endpoint_receipts WHERE article=? AND mode='current_stable'
        ORDER BY checked_at DESC LIMIT 1""", [article]).fetchone()
    if not row:
        return None
    columns = (
        "mode", "latest_seen_revid", "selected_revid", "selected_snapshot_date",
        "selected_timestamp", "survival_seconds", "confirmed_by_revid", "status",
        "excluded_revisions", "policy_version", "checked_at",
    )
    receipt = dict(zip(columns, row))
    receipt["article"] = article
    receipt["excluded_revisions"] = json.loads(receipt["excluded_revisions"])
    return receipt


def audit_stable_endpoints(con, articles=None, observed_at=None, persist=False):
    """Report endpoint stability, optionally persisting receipts as an explicit backfill."""
    if persist:
        ensure_schema(con)
    targets = articles or [row[0] for row in con.execute(
        "SELECT DISTINCT article FROM rsnap ORDER BY article"
    ).fetchall()]
    reports = []
    totals = {"stable": 0, "unstable": 0}
    for article in targets:
        receipt = (
            refresh_stable_endpoint(con, article, observed_at=observed_at)
            if persist else assess_stable_endpoint(con, article, observed_at=observed_at)[0]
        )
        totals[receipt["status"]] += 1
        reports.append({
            "article": article,
            "status": receipt["status"],
            "latest_seen_revid": receipt["latest_seen_revid"],
            "selected_revid": receipt["selected_revid"],
            "excluded_revisions": receipt["excluded_revisions"],
        })
    return {
        "policy_version": STABLE_ENDPOINT_POLICY,
        "article_count": len(reports),
        "totals": totals,
        "articles": reports,
    }


def record_article_identity(con, resolved):
    """Persist the requested alias and canonical MediaWiki identity additively."""
    ensure_schema(con)
    con.execute(
        "DELETE FROM article_identity WHERE requested_title=?",
        [resolved.requested_title],
    )
    con.execute(
        "INSERT INTO article_identity VALUES (?,?,?,?)",
        [resolved.requested_title, resolved.canonical_title, resolved.page_id,
         dt.datetime.now(dt.timezone.utc).isoformat()],
    )


def load_source_state(con, article):
    """Return persisted source and snapshot coverage for one article, if measured."""
    exists = con.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='main' AND table_name='article_source_state'""").fetchone()[0]
    if not exists:
        return None
    row = con.execute("""SELECT source_status, source_checked_at, source_latest_revid,
        expected_snapshots, loaded_snapshots, reason
        FROM article_source_state WHERE article=?""", [article]).fetchone()
    if not row:
        return None
    keys = (
        "source_status", "source_checked_at", "source_latest_revid",
        "expected_snapshots", "loaded_snapshots", "reason",
    )
    return {"article": article, **dict(zip(keys, row))}


def record_source_state(con, article, **updates):
    """Merge measured source coverage into the article's idempotent state row."""
    ensure_schema(con)
    current = load_source_state(con, article) or {"article": article}
    current.update(updates)
    values = [
        article,
        current.get("source_status", "unchecked"),
        current.get("source_checked_at"),
        current.get("source_latest_revid"),
        current.get("expected_snapshots"),
        current.get("loaded_snapshots"),
        current.get("reason"),
    ]
    con.execute("""MERGE INTO article_source_state AS target
        USING (SELECT ? AS article, ? AS source_status, ? AS source_checked_at,
                      ? AS source_latest_revid, ? AS expected_snapshots,
                      ? AS loaded_snapshots, ? AS reason) AS source
        ON target.article = source.article
        WHEN MATCHED THEN UPDATE SET
            source_status = source.source_status,
            source_checked_at = source.source_checked_at,
            source_latest_revid = source.source_latest_revid,
            expected_snapshots = source.expected_snapshots,
            loaded_snapshots = source.loaded_snapshots,
            reason = source.reason
        WHEN NOT MATCHED THEN INSERT VALUES (
            source.article, source.source_status, source.source_checked_at,
            source.source_latest_revid, source.expected_snapshots,
            source.loaded_snapshots, source.reason)""", values)
    return load_source_state(con, article)


def ensure_indexes(con):
    """Load-bearing: rsnap grows to millions of rows; without these the per-date existence checks
    and interval joins become full-table scans (the perf pathology found in the base-rate run)."""
    ensure_schema(con)
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_rsnap_art_rev ON rsnap(article, snap_rev)",
        "CREATE INDEX IF NOT EXISTS ix_rsnap_art_tok ON rsnap(article, token_id)",
        "CREATE INDEX IF NOT EXISTS ix_snapint_art_rev ON snapshot_integrity(article, snap_rev)",
        "CREATE INDEX IF NOT EXISTS ix_endpoint_art_mode ON endpoint_receipts(article, mode)",
        "CREATE INDEX IF NOT EXISTS ix_rev_art_id ON revisions(article, rev_id)",
        "CREATE INDEX IF NOT EXISTS ix_revmeta_art_id ON revision_metadata(article, rev_id)",
        "CREATE INDEX IF NOT EXISTS ix_revsize_art_id ON rev_size(article, rev_id)",
        "CREATE INDEX IF NOT EXISTS ix_tokens_art_tok ON tokens(article, token_id)",
    ):
        try:
            con.execute(stmt)
        except Exception as ex:                          # noqa: BLE001
            # These indexes are load-bearing (the base-rate perf pathology). CREATE INDEX IF NOT EXISTS
            # shouldn't raise on a normal re-run, so any failure is real — surface it, never swallow it.
            print(f"  !! WARNING: index not created ({stmt.split(' ON ')[0].split()[-1]}): {ex}", flush=True)


# --- Action API: timeline + sizes -------------------------------------------
def ensure_sizes(con, article):
    """Populate the revision timeline (rev_id->ts/user) and per-rev size. Commits per page so
    an interrupted run can resume: re-entry continues from the last stored revision instead of
    re-downloading the entire history."""
    ensure_schema(con)
    resolved = resolve_article_title(article)
    if resolved.canonical_title != article:
        raise ValueError(
            f"article title {article!r} redirects to {resolved.canonical_title!r}; "
            "use the canonical title before selecting storage"
        )
    record_article_identity(con, resolved)
    latest = con.execute("SELECT max(rev_id) FROM revisions WHERE article=?", [article]).fetchone()[0]

    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
              "redirects": 1, "titles": article,
              "rvprop": "ids|timestamp|user|size|comment|tags|sha1|flags",
              "rvlimit": "max", "rvdir": "newer", "maxlag": "5"}
    if latest:
        params["rvstartid"] = latest   # resume from last known rev (inclusive — deduped below)

    # Load existing IDs only when resuming so we can skip the boundary rev returned by rvstartid
    seen = ({r[0] for r in con.execute("SELECT rev_id FROM revisions WHERE article=?", [article]).fetchall()}
            if latest else set())
    total = len(seen)

    while True:
        try:
            d = config.get_json_retrying(_S, config.ACTION, params=params)
        except Exception as exc:
            record_source_state(
                con,
                article,
                source_status="unavailable",
                source_checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_latest_revid=latest,
                reason=f"history retrieval failed: {exc}",
            )
            raise
        revrows, szrows, metadata_rows = [], [], []
        retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
        for pg in d.get("query", {}).get("pages", []):
            for rv in pg.get("revisions", []):
                rid = int(rv["revid"])
                if rid in seen:
                    continue
                seen.add(rid)
                revrows.append((article, rid, rv["timestamp"], rv.get("user", "<hidden>")))
                szrows.append((article, rid, int(rv.get("size", 0))))
                metadata_rows.append((
                    article, rid, rv.get("parentid"), rv.get("sha1"), rv.get("comment"),
                    json.dumps(rv.get("tags", []), ensure_ascii=False, sort_keys=True),
                    bool(rv.get("minor", False)), "userhidden" in rv, retrieved_at,
                ))
        if revrows:
            con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", revrows)
            con.executemany("INSERT INTO rev_size VALUES (?,?,?)", szrows)
            con.executemany("INSERT INTO revision_metadata VALUES (?,?,?,?,?,?,?,?,?)", metadata_rows)
            total += len(revrows)
        print(f"\r  history: {total:,} revisions…", end="", flush=True)
        if "continue" in d:
            params.pop("rvstartid", None)   # rvcontinue supersedes rvstartid
            params["rvcontinue"] = d["continue"]["rvcontinue"]
        else:
            break
    print(f"\r  history: {total:,} revisions" + " " * 10, flush=True)
    source_latest_revid = con.execute(
        "SELECT max(rev_id) FROM revisions WHERE article=?", [article]
    ).fetchone()[0]
    record_source_state(
        con,
        article,
        source_status="history_complete",
        source_checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_latest_revid=source_latest_revid,
        reason=None,
    )


# --- WikiWho: per-revision tokens -------------------------------------------
def tokens_at(article, rev_id, io=False):
    """Surviving tokens of `article` at `rev_id`, cached. io=True also returns in/out lifecycle."""
    key = (rev_id, io)
    if key in _tok:
        _tok.move_to_end(key)                      # LRU: mark as recently used
        return _tok[key]
    extra = "&out=true&in=true" if io else "&out=false&in=false"
    url = f"{config.WIKIWHO}/rev_content/{urllib.parse.quote(article, safe='')}/{rev_id}/?token_id=true&o_rev_id=true{extra}"
    for attempt in range(4):                       # retry: WikiWho throws transient errors under load
        try:
            d = _S.get(url, timeout=25).json()     # fail fast; WMCloud is flaky, don't hang 180s per call
            if d.get("revisions"):
                rev = d["revisions"][0]; rid = next(iter(rev))
                return _tok_put(key, rev[rid]["tokens"])
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return _tok_put(key, [])                        # unavailable after retries -> caller skips


# --- snapshotting -----------------------------------------------------------
def persistent_rev(con, article, date, window_days=21):
    """Pick a revision near `date` whose size ~ the local median (rejects transient blanking)."""
    rows = con.execute("""
        SELECT r.rev_id, r.ts, z.size,
               abs(date_diff('day', CAST(r.ts AS TIMESTAMP), CAST(? AS TIMESTAMP))) AS dd
        FROM revisions r JOIN rev_size z ON z.article=r.article AND z.rev_id=r.rev_id
        WHERE r.article=? AND abs(date_diff('day', CAST(r.ts AS TIMESTAMP), CAST(? AS TIMESTAMP))) <= ?
        ORDER BY dd
    """, [date + " 00:00:00", article, date + " 00:00:00", window_days]).fetchall()
    if not rows:
        r = con.execute("""SELECT r.rev_id, r.ts FROM revisions r
            WHERE r.article=? AND r.ts<=? ORDER BY r.ts DESC LIMIT 1""", [article, date + "T00:00:00Z"]).fetchone()
        return r
    med = statistics.median([s for _, _, s, _ in rows])
    for rev_id, ts, size, _ in rows:               # nearest-to-date rev whose size is within 25% of median
        if med and abs(size - med) <= 0.25 * med:
            return (rev_id, ts)
    return (rows[0][0], rows[0][1])


def snapshot_picks(con, article):
    """The persistent-revision snapshot dates + rev_ids (adaptive cadence, size ~ local median).
    Backend-agnostic selection shared by build_snapshots (hosted WikiWho) and ingest (local wikiwho_rs),
    so both populate rsnap from the *same* revisions."""
    first = con.execute("SELECT min(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    last = con.execute("SELECT max(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    total_revs = con.execute("SELECT count(*) FROM revisions WHERE article=?", [article]).fetchone()[0]
    if not first or not last:
        return []
    # adaptive cadence: annual for large articles (keeps rsnap small); binary search refines timing anyway
    months = (1, 7) if total_revs <= 8000 else (1,)
    dates = [f"{y}-{m:02d}-01" for y in range(int(first[:4]), int(last[:4]) + 1) for m in months]
    picks = []
    for d in dates:
        pr = persistent_rev(con, article, d)
        if not pr or (picks and pr[0] == picks[-1][1]):
            continue
        picks.append((d, pr[0]))
    return picks


def _pbar(done, total, width=20):
    filled = int(width * done / total) if total else width
    return "█" * filled + "░" * (width - filled)


def build_snapshots(con, article):
    """Build persistent-revision snapshots into rsnap from the HOSTED WikiWho API (polite)."""
    ensure_schema(con)
    picks = snapshot_picks(con, article)
    n = len(picks)
    snaps = []
    pending_rows = []
    for i, (d, rev_id) in enumerate(picks, 1):
        cached = bool(con.execute("SELECT 1 FROM rsnap WHERE article=? AND snap_rev=?", [article, rev_id]).fetchone())
        label = "cached " if cached else "fetch  "
        print(f"\r  snapshots [{_pbar(i, n)}] {i}/{n}  {d}  {label}", end="", flush=True)
        if not cached:
            toks = tokens_at(article, rev_id)
            if not toks:
                continue                            # WikiWho couldn't serve this revision — skip snapshot
            pending_rows.extend(
                (article, d, rev_id, t["token_id"], t["o_rev_id"]) for t in toks
            )
            time.sleep(0.3)                         # be polite to the WikiWho API
        snaps.append((d, rev_id))
    if n:
        print(f"\r  snapshots [{_pbar(n, n)}] {len(snaps)}/{n} loaded" + " " * 20, flush=True)
    con.execute("BEGIN")
    try:
        if pending_rows:
            con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", pending_rows)
        integrity = refresh_snapshot_integrity(con, article)
        refresh_stable_endpoint(con, article)
        quarantined = [receipt for receipt in integrity if receipt["status"] == "quarantined"]
        complete = bool(n) and len(snaps) == n and not quarantined
        reason = None
        if quarantined:
            reason = f"{len(quarantined)} snapshot(s) failed integrity checks"
        elif not complete:
            reason = f"loaded {len(snaps)} of {n} expected snapshots"
        record_source_state(
            con,
            article,
            source_status="current_complete" if complete else "partial",
            expected_snapshots=n,
            loaded_snapshots=len(snaps),
            reason=reason,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return snaps

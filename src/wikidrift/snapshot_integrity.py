"""Snapshot semantic-integrity policy and corpus audits.

Extracted from provenance.py so integrity policy changes do not sit beside fetch/resume logic.
Public names remain re-exported from `wikidrift.provenance`.
"""
from __future__ import annotations

import datetime as dt

SNAPSHOT_INTEGRITY_POLICY = "snapshot-integrity-v1"
MIN_NEIGHBOR_TOKEN_RATIO = 0.5
MIN_NEIGHBOR_BYTE_RATIO = 0.8


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

def refresh_snapshot_integrity(con, article):
    """Recompute and persist integrity receipts for all loaded snapshots of one article."""
    from . import provenance
    provenance.ensure_schema(con)
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
    from . import provenance
    provenance.ensure_schema(con)
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
    from . import provenance
    if persist:
        provenance.ensure_schema(con)
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
            current = provenance.load_source_state(con, article) or {}
            provenance.record_source_state(
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


"""Stable current-endpoint policy and corpus audits.

Extracted from provenance.py so endpoint policy changes do not sit beside fetch/resume logic.
Public names remain re-exported from `wikidrift.provenance`.
"""
from __future__ import annotations

import datetime as dt
import json

STABLE_ENDPOINT_POLICY = "stable-endpoint-v1"
MIN_ENDPOINT_SURVIVAL_SECONDS = 48 * 60 * 60


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


def refresh_stable_endpoint(con, article, observed_at=None):
    """Recompute and persist the current stable endpoint receipt for one article."""
    from . import provenance
    provenance.ensure_schema(con)
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
    snapshots = con.execute(f"""
        SELECT DISTINCT s.snap_date, s.snap_rev, r.ts
        FROM rsnap s
        LEFT JOIN revisions r ON r.article=s.article AND r.rev_id=s.snap_rev
        WHERE s.article=? AND {integrity_clause}
        ORDER BY s.snap_date, s.snap_rev
    """, [article]).fetchall()
    checked_at = observed_at or dt.datetime.now(dt.timezone.utc)
    receipt = select_stable_endpoint(snapshots, checked_at)
    return receipt, checked_at


def load_stable_endpoint(con, article):
    """Return the persisted current stable endpoint receipt, or None."""
    from . import provenance
    provenance.ensure_schema(con)
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
    from . import provenance
    if persist:
        provenance.ensure_schema(con)
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


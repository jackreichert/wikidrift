"""Shared publication-trust decisions for endpoint-relative findings artifacts."""


def resolve_artifact_trust(con, article, artifact, artifact_kind):
    """Return whether an artifact has compatible snapshot and endpoint evidence."""
    endpoint = _endpoint_receipt(con, article)
    if endpoint is None:
        return _withheld("legacy_incompatible", "stable endpoint receipt is missing")
    selected_revid, endpoint_status, policy_version = endpoint
    if endpoint_status != "stable" or selected_revid is None:
        return _withheld("unstable", "current endpoint is unstable")

    referenced_revisions = _referenced_revisions(artifact, artifact_kind)
    if artifact_kind == "l1-confirmation":
        saved = artifact.get("corpus_horizon") or {}
        if saved.get("snapshot_revid") != selected_revid:
            return _withheld("stale", "artifact endpoint does not match selected stable endpoint")
    elif artifact_kind == "lexical":
        after_revid = ((artifact.get("after") or {}).get("rev"))
        if after_revid is None:
            return _withheld("legacy_incompatible", "lexical artifact lacks revision evidence")
        if artifact.get("interval_source") == "snapshot_endpoints" and after_revid != selected_revid:
            return _withheld("stale", "lexical endpoint does not match selected stable endpoint")
    elif artifact_kind == "stance":
        if not referenced_revisions:
            return _withheld("legacy_incompatible", "stance artifact lacks revision evidence")

    integrity_revisions = _integrity_revisions(artifact, artifact_kind)
    integrity_statuses = _integrity_statuses(con, article, integrity_revisions)
    if integrity_statuses is None:
        return _withheld("legacy_incompatible", "snapshot integrity receipts are missing")
    missing = integrity_revisions - set(integrity_statuses)
    if missing:
        revisions = ", ".join(str(revision_id) for revision_id in sorted(missing))
        return _withheld(
            "legacy_incompatible",
            f"snapshot integrity receipt is missing for revision(s): {revisions}",
        )
    quarantined = {
        revision_id for revision_id, status in integrity_statuses.items()
        if status == "quarantined"
    }
    if quarantined:
        revisions = ", ".join(str(revision_id) for revision_id in sorted(quarantined))
        return _withheld("quarantined", f"artifact references quarantined revision(s): {revisions}")

    return {
        "status": "published",
        "reason": None,
        "endpoint_policy_version": policy_version,
    }


def _endpoint_receipt(con, article):
    if con is None or not _table_exists(con, "endpoint_receipts"):
        return None
    return con.execute("""SELECT selected_revid, status, policy_version
        FROM endpoint_receipts WHERE article=? AND mode='current_stable'
        ORDER BY checked_at DESC LIMIT 1""", [article]).fetchone()


def _referenced_revisions(artifact, artifact_kind):
    if artifact_kind == "l1-confirmation":
        revisions = {
            (artifact.get("corpus_horizon") or {}).get("snapshot_revid"),
        }
        for episode in artifact.get("confirmed_episodes") or []:
            revisions.update((episode.get("before_revid"), episode.get("after_revid")))
        return {revision_id for revision_id in revisions if revision_id is not None}
    if artifact_kind == "lexical":
        return {
            revision_id for revision_id in (
                (artifact.get("before") or {}).get("rev"),
                (artifact.get("after") or {}).get("rev"),
            ) if revision_id is not None
        }
    if artifact_kind == "stance":
        return {
            row.get("revision_id")
            for row in artifact.get("classifications") or []
            if row.get("revision_id") is not None
        }
    return set()


def _integrity_revisions(artifact, artifact_kind):
    if artifact_kind == "l1-confirmation":
        revision_id = (artifact.get("corpus_horizon") or {}).get("snapshot_revid")
        return {revision_id} if revision_id is not None else set()
    if artifact_kind == "lexical" and artifact.get("interval_source") == "snapshot_endpoints":
        return _referenced_revisions(artifact, artifact_kind)
    if artifact_kind == "stance":
        return _referenced_revisions(artifact, artifact_kind)
    return set()


def _integrity_statuses(con, article, revision_ids):
    if not revision_ids:
        return {}
    if con is None or not _table_exists(con, "snapshot_integrity"):
        return None
    placeholders = ",".join("?" for _ in revision_ids)
    rows = con.execute(
        f"SELECT snap_rev, status FROM snapshot_integrity WHERE article=? "
        f"AND snap_rev IN ({placeholders})",
        [article, *sorted(revision_ids)],
    ).fetchall()
    return dict(rows)


def _table_exists(con, table_name):
    return bool(con.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?""", [table_name]).fetchone()[0])


def _withheld(status, reason):
    return {"status": status, "reason": reason, "endpoint_policy_version": None}

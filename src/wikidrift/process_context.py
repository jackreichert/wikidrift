"""Neutral editorial-process receipts for bounded exact events.

Process metadata can suggest alternatives worth inspecting, but it cannot confirm a content finding or
establish identity, coordination, motive, ownership, factual quality, or misconduct.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse

from . import config


PROCESS_CONTEXT_SCHEMA_VERSION = 1
PROCESS_CONTEXT_POLICY_VERSION = "editorial-process-context-v1"
TALK_CONTEXT_WINDOW = dt.timedelta(hours=24)
_SECTION_COMMENT = re.compile(r"^/\*\s*(.*?)\s*\*/")
_REVERT_TAGS = {"mw-rollback", "mw-undo", "mw-reverted", "mw-manual-revert"}
_DISPUTE_TEMPLATE = re.compile(
    r"(?:disput|pov|contentious|neutrality|merge|split)", re.IGNORECASE
)
_SESSION = config.session()


def build_process_context(
        article, episode, revision_metadata, *, talk_revisions=None, log_events=None,
        protection=None, dispute_templates=None, arbitration=None, retrieved_at=None):
    """Normalize bounded public process metadata into an evidence-preserving receipt."""
    retrieved_at = retrieved_at or dt.datetime.now(dt.timezone.utc).isoformat()
    revision_activity = [_revision_item(article, row) for row in revision_metadata]
    talk_activity = [_talk_item(article, row) for row in (talk_revisions or [])]
    page_operations = [_log_item(article, row) for row in (log_events or [])]
    revert_relationships = _revert_relationships(article, revision_metadata)
    protection = protection or {"status": "not_observed", "items": []}
    dispute_templates = dispute_templates or {"status": "not_observed", "items": []}
    arbitration = arbitration or {"status": "not_observed", "items": []}

    availability = {
        "revision_activity": _availability(revision_activity),
        "revert_relationships": _availability(revert_relationships),
        "talk_activity": _availability(talk_activity),
        "page_operations": _availability(page_operations),
        "protection": _source_availability(protection),
        "dispute_templates": _source_availability(dispute_templates),
        "arbitration": _source_availability(arbitration),
    }
    return {
        "schema_version": PROCESS_CONTEXT_SCHEMA_VERSION,
        "policy_version": PROCESS_CONTEXT_POLICY_VERSION,
        "article": article,
        "before_revid": episode["before_revid"],
        "before_timestamp": episode["before_timestamp"],
        "after_revid": episode["after_revid"],
        "after_timestamp": episode["after_timestamp"],
        "retrieved_at": retrieved_at,
        "semantic_role": "descriptive_process_context",
        "affects_confirmation": False,
        "affects_corroboration": False,
        "revision_activity": revision_activity,
        "revert_relationships": revert_relationships,
        "talk_activity": talk_activity,
        "page_operations": page_operations,
        "protection": protection.get("items", []),
        "dispute_templates": dispute_templates.get("items", []),
        "arbitration": arbitration.get("items", []),
        "availability": availability,
        "interpretation_note": (
            "Process signals provide alternatives for revision-level review. They do not establish "
            "identity, coordination, motive, ownership, factual quality, bias, or misconduct."
        ),
    }


def retrieve_process_context(article, episode):
    """Fetch bounded public process evidence, preserving failures per evidence family."""
    revisions, revision_availability = _retrieve_family(
        "revision activity", lambda: _fetch_revision_activity(article, episode)
    )
    talk, talk_availability = _retrieve_family(
        "talk activity", lambda: _fetch_talk_activity(article, episode)
    )
    operations, operation_availability = _retrieve_family(
        "page operations", lambda: _fetch_page_operations(article, episode)
    )
    protection_items, protection_availability = _retrieve_family(
        "protection", lambda: _fetch_protection(article)
    )
    templates, template_availability = _retrieve_family(
        "dispute templates", lambda: _fetch_dispute_templates(article, episode["after_revid"])
    )
    receipt = build_process_context(
        article,
        episode,
        revisions,
        talk_revisions=talk,
        log_events=operations,
        protection={**protection_availability, "items": protection_items},
        dispute_templates={**template_availability, "items": templates},
        arbitration={
            "status": "unavailable",
            "reason": "no bounded public arbitration source is configured",
            "items": [],
        },
    )
    receipt["availability"].update({
        "revision_activity": revision_availability,
        "talk_activity": talk_availability,
        "page_operations": operation_availability,
    })
    if revision_availability["status"] == "unavailable":
        receipt["availability"]["revert_relationships"] = {
            "status": "unavailable",
            "reason": revision_availability["reason"],
        }
    return receipt


def _retrieve_family(name, fetch):
    try:
        items = fetch()
    except Exception as exc:  # noqa: BLE001 - each public source degrades independently
        return [], {"status": "unavailable", "reason": f"{name} retrieval failed: {exc}"}
    return items, _availability(items)


def _fetch_revision_activity(article, episode):
    params = _revision_params(article, episode["before_timestamp"], episode["after_timestamp"])
    return _revision_rows(_query_all(params), article)


def _fetch_talk_activity(article, episode):
    start = _parse_timestamp(episode["before_timestamp"]) - TALK_CONTEXT_WINDOW
    end = _parse_timestamp(episode["after_timestamp"]) + TALK_CONTEXT_WINDOW
    params = _revision_params(
        f"Talk:{article}", start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )
    return _revision_rows(_query_all(params), f"Talk:{article}")


def _fetch_page_operations(article, episode):
    params = {
        "action": "query", "format": "json", "formatversion": "2", "list": "logevents",
        "letitle": article, "lestart": episode["before_timestamp"],
        "leend": episode["after_timestamp"], "ledir": "newer", "lelimit": "max",
        "leprop": "ids|title|type|user|timestamp|comment|details|tags", "maxlag": "5",
    }
    rows = []
    for response in _query_all(params):
        for event in response.get("query", {}).get("logevents", []):
            rows.append({
                "log_id": event["logid"], "timestamp": event["timestamp"],
                "type": event.get("type"), "action": event.get("action"),
                "account": event.get("user", "<hidden>"), "comment": event.get("comment", ""),
                "details": event.get("params") or {}, "tags": event.get("tags") or [],
            })
    return rows


def _fetch_protection(article):
    params = {
        "action": "query", "format": "json", "formatversion": "2", "prop": "info",
        "titles": article, "inprop": "protection", "maxlag": "5",
    }
    response = config.get_json_retrying(_SESSION, config.ACTION, params=params)
    pages = response.get("query", {}).get("pages", [])
    if not pages:
        return []
    return [{
        "type": item.get("type"), "level": item.get("level"),
        "expiry": item.get("expiry"), "source_url": _page_url(article),
    } for item in pages[0].get("protection", [])]


def _fetch_dispute_templates(article, revision_id):
    params = {
        "action": "query", "format": "json", "formatversion": "2", "prop": "templates",
        "revids": revision_id, "tllimit": "max", "tlprop": "title", "maxlag": "5",
    }
    items = []
    for response in _query_all(params):
        for page in response.get("query", {}).get("pages", []):
            for template in page.get("templates", []):
                title = template.get("title", "")
                if _DISPUTE_TEMPLATE.search(title):
                    items.append({
                        "title": title,
                        "revision_id": revision_id,
                        "source_url": _oldid_url(article, revision_id),
                    })
    return items


def _revision_params(title, start, end):
    return {
        "action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
        "titles": title, "rvstart": start, "rvend": end, "rvdir": "newer", "rvlimit": "max",
        "rvprop": "ids|timestamp|user|size|comment|tags|sha1|flags", "maxlag": "5",
    }


def _query_all(params):
    current = dict(params)
    responses = []
    while True:
        response = config.get_json_retrying(_SESSION, config.ACTION, params=current)
        responses.append(response)
        continuation = response.get("continue")
        if not continuation:
            return responses
        current.update(continuation)


def _revision_rows(responses, title):
    rows = []
    for response in responses:
        for page in response.get("query", {}).get("pages", []):
            for revision in page.get("revisions", []):
                rows.append({
                    "revision_id": revision["revid"], "parent_id": revision.get("parentid"),
                    "timestamp": revision["timestamp"], "account": revision.get("user", "<hidden>"),
                    "sha1": revision.get("sha1"), "comment": revision.get("comment", ""),
                    "tags": revision.get("tags") or [], "size": revision.get("size"),
                    "source_title": title,
                })
    return rows


def _parse_timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _revision_item(article, row):
    return {
        "revision_id": row["revision_id"],
        "parent_id": row.get("parent_id"),
        "timestamp": row["timestamp"],
        "account": row.get("account", "<hidden>"),
        "comment": row.get("comment") or "",
        "section": _section(row.get("comment")),
        "tags": sorted(row.get("tags") or []),
        "sha1": row.get("sha1"),
        "size": row.get("size"),
        "source_url": _oldid_url(article, row["revision_id"]),
    }


def _talk_item(article, row):
    return {
        "revision_id": row["revision_id"],
        "timestamp": row["timestamp"],
        "account": row.get("account", "<hidden>"),
        "comment": row.get("comment") or "",
        "section": _section(row.get("comment")),
        "tags": sorted(row.get("tags") or []),
        "source_url": _oldid_url(f"Talk:{article}", row["revision_id"]),
    }


def _log_item(article, row):
    log_id = row["log_id"]
    return {
        "log_id": log_id,
        "timestamp": row["timestamp"],
        "type": row.get("type"),
        "action": row.get("action"),
        "account": row.get("account", "<hidden>"),
        "comment": row.get("comment") or "",
        "details": row.get("details") or {},
        "source_url": f"https://en.wikipedia.org/w/index.php?title=Special:Log&logid={log_id}",
        "article": article,
    }


def _revert_relationships(article, rows):
    sha_revisions = {}
    relationships = []
    for row in rows:
        revision_id = row["revision_id"]
        sha1 = row.get("sha1")
        tags = set(row.get("tags") or [])
        signals = sorted(tags & _REVERT_TAGS)
        restores_revision_id = sha_revisions.get(sha1) if sha1 else None
        if restores_revision_id is not None:
            signals.append("sha1_restoration")
        if signals:
            relationships.append({
                "revision_id": revision_id,
                "parent_id": row.get("parent_id"),
                "restores_revision_id": restores_revision_id,
                "signals": signals,
                "source_url": _oldid_url(article, revision_id),
            })
        if sha1:
            sha_revisions[sha1] = revision_id
    return relationships


def _section(comment):
    match = _SECTION_COMMENT.match(comment or "")
    return match.group(1).strip() if match else None


def _availability(items):
    return {"status": "observed" if items else "not_observed", "reason": None}


def _source_availability(source):
    status = source.get("status", "unavailable")
    if status not in {"observed", "not_observed", "unavailable"}:
        raise ValueError(f"unsupported process-context availability status: {status}")
    return {"status": status, "reason": source.get("reason")}


def _oldid_url(article, revision_id):
    title = urllib.parse.quote(article.replace(" ", "_"), safe="():/")
    return f"https://en.wikipedia.org/w/index.php?title={title}&oldid={revision_id}"


def _page_url(article):
    title = urllib.parse.quote(article.replace(" ", "_"), safe="():/")
    return f"https://en.wikipedia.org/wiki/{title}"

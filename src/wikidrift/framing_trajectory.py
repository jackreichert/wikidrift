"""Deterministic addition-side and formative framing trajectories.

This module emits inspectable research leads from exact revision text. It does not infer bias,
intent, factual error, or misconduct.
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from collections import defaultdict, deque
from difflib import SequenceMatcher

import duckdb
import mwparserfromhell

from . import config
from .corpus import Corpus


TRAJECTORY_SCHEMA_VERSION = 1
TRAJECTORY_POLICY_VERSION = "framing-trajectory-v1"
MIN_MATCH_SIMILARITY = 0.9

_REF_RE = re.compile(r"<ref\b[^>]*>.*?</ref\s*>|<ref\b[^>]*/\s*>", re.I | re.S)
_URL_RE = re.compile(r"https?://[^\s<>{}\]|]+", re.I)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_MARKER_RE = re.compile(r"__WDREF_(\d+)__")

_S = config.session()


def analyze_article(article, mode="formative", start=None, end=None, revision_ids=None,
                    persist=True, fetch_revision=None):
    """Build and optionally persist a trajectory from exact public revisions.

    Formative and interval modes select integrity-usable corpus snapshots through the stable endpoint.
    Exact-event mode uses the caller's explicit ordered revision IDs.
    """
    article = article.strip()
    if not article:
        raise ValueError("article is required")
    selected = _select_article_revisions(article, mode, start, end, revision_ids)
    fetch = fetch_revision or _fetch_revision
    revisions = []
    for selected_revision in selected:
        fetched = fetch(selected_revision["revision_id"])
        if not fetched:
            return _unavailable_receipt(
                article, mode, selected,
                f"exact revision {selected_revision['revision_id']} is unavailable",
                persist,
            )
        revisions.append(fetched)
    result = analyze_revision_sequence(article, revisions, mode=mode)
    result["status"] = "available"
    result["selection"] = {
        "start": start,
        "end": end,
        "requested_revision_ids": list(revision_ids or []),
        "selected_revision_ids": [revision["revision_id"] for revision in selected],
    }
    if persist:
        _persist(article, result)
    return result


def _select_article_revisions(article, mode, start, end, revision_ids):
    if mode == "exact_event":
        selected_ids = [int(revision_id) for revision_id in (revision_ids or [])]
        if len(selected_ids) < 2:
            raise ValueError("exact_event mode requires at least two revision IDs")
        return [{"revision_id": revision_id, "snapshot_date": None} for revision_id in selected_ids]
    if mode not in {"formative", "interval"}:
        raise ValueError(f"unsupported framing trajectory mode: {mode}")
    if not config.DB.exists():
        raise ValueError("snapshot corpus is unavailable")
    con = duckdb.connect(str(config.DB), read_only=True)
    try:
        corpus = Corpus(con)
        snapshots = corpus.snapshots(article)
        endpoint = corpus.latest_snapshot(article)
    finally:
        con.close()
    if endpoint is None:
        raise ValueError("no stable endpoint is available")
    endpoint_index = next(
        (index for index, snapshot in enumerate(snapshots) if snapshot[1] == endpoint[1]),
        None,
    )
    if endpoint_index is None:
        raise ValueError("stable endpoint is not an integrity-usable snapshot")
    snapshots = snapshots[:endpoint_index + 1]
    if mode == "interval":
        if not start or not end:
            raise ValueError("interval mode requires start and end dates")
        snapshots = [
            snapshot for snapshot in snapshots
            if start <= str(snapshot[0]) <= end
        ]
    if len(snapshots) < 2:
        raise ValueError(f"{mode} mode requires at least two usable revisions")
    return [
        {"snapshot_date": str(snapshot_date), "revision_id": int(revision_id)}
        for snapshot_date, revision_id in snapshots
    ]


def _fetch_revision(revision_id):
    data = config.get_json_retrying(
        _S,
        config.ACTION,
        params={
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "revisions",
            "revids": revision_id,
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
        },
        timeout=30,
    )
    try:
        revision = data["query"]["pages"][0]["revisions"][0]
        return {
            "revision_id": int(revision["revid"]),
            "timestamp": revision["timestamp"],
            "wikitext": revision["slots"]["main"]["content"],
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _unavailable_receipt(article, mode, selected, reason, persist):
    result = {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "policy_version": TRAJECTORY_POLICY_VERSION,
        "article": article,
        "mode": mode,
        "status": "unavailable",
        "semantic_role": "framing_change_lead",
        "reason": reason,
        "selection": {
            "selected_revision_ids": [revision["revision_id"] for revision in selected],
        },
        "framing_change_lead": False,
        "events": [],
    }
    if persist:
        _persist(article, result)
    return result


def _persist(article, result):
    config.write_findings(f"{config.slugify(article)}.framing-trajectory.json", result)


def analyze_revision_sequence(article, revisions, mode="formative"):
    """Compare an ordered revision sequence and classify addition persistence."""
    if len(revisions) < 2:
        raise ValueError("at least two revisions are required")
    documents = [_parse_revision(article, revision) for revision in revisions]
    revision_ids = [document["revision_id"] for document in documents]
    if len(set(revision_ids)) != len(revision_ids):
        raise ValueError("revision IDs must be unique")

    events = []
    for index, (before, after) in enumerate(zip(documents, documents[1:])):
        event = _compare_documents(before, after)
        later_documents = documents[index + 2:]
        for addition in event["added"]:
            persistence = [
                document["revision_id"]
                for document in later_documents
                if _unit_present(addition, document["units"])
            ]
            addition["introduced_revision_id"] = after["revision_id"]
            addition["persistence_revisions"] = persistence
            addition["persistence_snapshots"] = len(persistence)
            addition["standing"] = bool(
                after["revision_id"] == documents[-1]["revision_id"]
                or (later_documents and later_documents[-1]["revision_id"] in persistence)
            )
        events.append(event)

    standing_additions = [
        addition
        for event in events
        for addition in event["added"]
        if addition["standing"]
    ]
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "policy_version": TRAJECTORY_POLICY_VERSION,
        "article": article,
        "mode": mode,
        "semantic_role": "framing_change_lead",
        "framing_change_lead": bool(standing_additions),
        "revisions": [document["receipt"] for document in documents],
        "events": events,
        "summary": {
            "events": len(events),
            "standing_additions": len(standing_additions),
            "transient_additions": sum(
                not addition["standing"]
                for event in events
                for addition in event["added"]
            ),
        },
        "note": (
            "Addition and relocation signals are research leads from observable text changes, "
            "not findings of bias, intent, factual error, or misconduct."
        ),
    }


def _parse_revision(article, revision):
    revision_id = int(revision["revision_id"])
    timestamp = revision.get("timestamp")
    raw = revision.get("wikitext") or ""
    code = mwparserfromhell.parse(raw)
    sections_with_headings = code.get_sections(include_lead=True, include_headings=True)
    section_bodies = code.get_sections(include_lead=True, include_headings=False)
    units = []
    for section_index, body in enumerate(section_bodies):
        section = _section_title(sections_with_headings, section_index)
        location = "lead" if section_index == 0 else "body"
        for unit_index, sentence in enumerate(_sentences_with_citations(str(body))):
            normalized = _normalize(sentence["text"])
            if not normalized:
                continue
            units.append({
                "claim_id": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20],
                "text": sentence["text"],
                "normalized_text": normalized,
                "revision_id": revision_id,
                "timestamp": timestamp,
                "oldid_url": _oldid_url(article, revision_id),
                "section": section,
                "location": location,
                "unit_index": unit_index,
                "citation_domains": sentence["citation_domains"],
                "citation_receipts": sentence["citation_receipts"],
            })
    return {
        "revision_id": revision_id,
        "timestamp": timestamp,
        "units": units,
        "receipt": {
            "revision_id": revision_id,
            "timestamp": timestamp,
            "oldid_url": _oldid_url(article, revision_id),
            "content_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "prose_characters": sum(len(unit["text"]) for unit in units),
            "claim_units": len(units),
        },
    }


def _section_title(sections_with_headings, section_index):
    if section_index == 0:
        return "Lead"
    headings = sections_with_headings[section_index].filter_headings(recursive=False)
    if not headings:
        return f"Section {section_index}"
    return mwparserfromhell.parse(str(headings[0].title)).strip_code().strip()


def _sentences_with_citations(raw_section):
    references = []

    def replace_reference(match):
        references.append(match.group(0))
        return f" __WDREF_{len(references) - 1}__ "

    marked = _REF_RE.sub(replace_reference, raw_section)
    plain = mwparserfromhell.parse(marked).strip_code(normalize=True, collapse=True)
    plain = re.sub(r"([.!?])\s*(__WDREF_\d+__)", r" \2\1", plain)
    chunks = [chunk.strip() for chunk in _SENTENCE_RE.split(plain) if chunk.strip()]
    sentences = []
    for chunk in chunks:
        indexes = [int(index) for index in _MARKER_RE.findall(chunk)]
        text = re.sub(r"\s+", " ", _MARKER_RE.sub("", chunk)).strip()
        text = re.sub(r"\s+([.!?])", r"\1", text)
        if not text:
            continue
        citation_receipts = [_citation_receipt(references[index]) for index in indexes]
        citation_receipts = [receipt for receipt in citation_receipts if receipt]
        sentences.append({
            "text": text,
            "citation_domains": sorted({
                domain
                for receipt in citation_receipts
                for domain in receipt["domains"]
            }),
            "citation_receipts": citation_receipts,
        })
    return sentences


def _citation_receipt(raw_reference):
    urls = [url.rstrip(".,;)") for url in _URL_RE.findall(raw_reference)]
    domains = sorted({
        urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        for url in urls
        if urllib.parse.urlparse(url).netloc
    })
    if not domains and not raw_reference.strip():
        return None
    normalized = re.sub(r"\s+", " ", raw_reference).strip()
    return {
        "domains": domains,
        "reference_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _compare_documents(before, after):
    matches, unmatched_before, unmatched_after = _match_units(before["units"], after["units"])
    retained = []
    relocated = []
    for before_unit, after_unit, similarity in matches:
        if (
            before_unit["section"] == after_unit["section"]
            and before_unit["location"] == after_unit["location"]
        ):
            retained.append(_match_receipt(before_unit, after_unit, similarity))
        else:
            relocated.append({
                **_match_receipt(before_unit, after_unit, similarity),
                "text": after_unit["text"],
                "from_section": before_unit["section"],
                "to_section": after_unit["section"],
                "from_location": before_unit["location"],
                "to_location": after_unit["location"],
            })
    return {
        "before_revision_id": before["revision_id"],
        "after_revision_id": after["revision_id"],
        "added": [_public_unit(unit) for unit in unmatched_after],
        "removed": [_public_unit(unit) for unit in unmatched_before],
        "retained": retained,
        "relocated": relocated,
        "lead_weight": {
            "before": _lead_weight(before["units"]),
            "after": _lead_weight(after["units"]),
        },
        "section_weights": {
            "before": _section_weights(before["units"]),
            "after": _section_weights(after["units"]),
        },
        "section_changes": _section_changes(before["units"], after["units"]),
    }


def _match_units(before_units, after_units):
    after_by_claim = defaultdict(deque)
    for index, unit in enumerate(after_units):
        after_by_claim[unit["claim_id"]].append(index)
    matches = []
    matched_before = set()
    matched_after = set()
    for before_index, before_unit in enumerate(before_units):
        candidates = after_by_claim[before_unit["claim_id"]]
        while candidates and candidates[0] in matched_after:
            candidates.popleft()
        if candidates:
            after_index = candidates.popleft()
            matches.append((before_unit, after_units[after_index], 1.0))
            matched_before.add(before_index)
            matched_after.add(after_index)

    fuzzy_candidates = []
    for before_index, before_unit in enumerate(before_units):
        if before_index in matched_before:
            continue
        for after_index, after_unit in enumerate(after_units):
            if after_index in matched_after:
                continue
            similarity = SequenceMatcher(
                None, before_unit["normalized_text"], after_unit["normalized_text"]
            ).ratio()
            if similarity >= MIN_MATCH_SIMILARITY:
                fuzzy_candidates.append((similarity, before_index, after_index))
    for similarity, before_index, after_index in sorted(fuzzy_candidates, reverse=True):
        if before_index in matched_before or after_index in matched_after:
            continue
        matches.append((before_units[before_index], after_units[after_index], similarity))
        matched_before.add(before_index)
        matched_after.add(after_index)

    return (
        matches,
        [unit for index, unit in enumerate(before_units) if index not in matched_before],
        [unit for index, unit in enumerate(after_units) if index not in matched_after],
    )


def _unit_present(unit, candidate_units):
    normalized_text = unit.get("normalized_text") or _normalize(unit["text"])
    return any(
        unit["claim_id"] == candidate["claim_id"]
        or SequenceMatcher(
            None, normalized_text, candidate["normalized_text"]
        ).ratio() >= MIN_MATCH_SIMILARITY
        for candidate in candidate_units
    )


def _public_unit(unit):
    return {key: value for key, value in unit.items() if key != "normalized_text"}


def _match_receipt(before_unit, after_unit, similarity):
    return {
        "claim_id": before_unit["claim_id"],
        "before_revision_id": before_unit["revision_id"],
        "after_revision_id": after_unit["revision_id"],
        "before_text": before_unit["text"],
        "after_text": after_unit["text"],
        "similarity": round(similarity, 4),
        "citation_change": {
            "before_domains": before_unit["citation_domains"],
            "after_domains": after_unit["citation_domains"],
            "added_domains": sorted(
                set(after_unit["citation_domains"]) - set(before_unit["citation_domains"])
            ),
            "removed_domains": sorted(
                set(before_unit["citation_domains"]) - set(after_unit["citation_domains"])
            ),
        },
    }


def _lead_weight(units):
    total = sum(len(unit["text"]) for unit in units)
    lead = sum(len(unit["text"]) for unit in units if unit["location"] == "lead")
    return round(lead / total, 4) if total else 0.0


def _section_weights(units):
    characters = defaultdict(int)
    for unit in units:
        characters[unit["section"]] += len(unit["text"])
    total = sum(characters.values())
    return {
        section: round(count / total, 4) if total else 0.0
        for section, count in sorted(characters.items())
    }


def _section_changes(before_units, after_units):
    before_sections = {unit["section"] for unit in before_units if unit["location"] == "body"}
    after_sections = {unit["section"] for unit in after_units if unit["location"] == "body"}
    return {
        "created": sorted(after_sections - before_sections),
        "deleted": sorted(before_sections - after_sections),
    }


def _normalize(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def _oldid_url(article, revision_id):
    title = urllib.parse.quote(article.replace(" ", "_"), safe="")
    return f"https://en.wikipedia.org/w/index.php?title={title}&oldid={revision_id}"
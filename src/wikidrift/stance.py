"""L2 — LLM stance classifier over time (promoted from spike 010).

Turns "reframed" from a lead into a MEASURED signal (the VADER prototype in spike 006 was too weak;
Johnson 2025 warns sentiment conflates tone with viewpoint balance). Classifies stance on an
ENCYCLOPEDIC-NEUTRALITY axis (critical / neutral / sympathetic toward a focal entity + an NPOV-departure
flag), NOT sentiment. It is the discriminator the benchmark says L1 lacks: a benign large change (Climate's
restructuring) shows NO directional stance shift; a capture (Zionism/Nakba reframe) does.

Per snapshot (rsnap revs from drift.py): fetch wikitext (Action API), strip to prose, keep focal-entity
sentences, classify with Claude (structured output). Emit the stance trajectory and flag a directional
shift. SIGNAL, not proof — a real event can legitimately reshape framing.

Known limit (born-framed wall): L2-as-shift-detector is still temporal + internal, so like L1 it is blind
to born-framed articles (Nakba reads flat). Consistent NPOV flags are a signal, but adjudicating whether
that POV is legitimate or capture needs L5 (external reference).
"""
import datetime as dt
import hashlib
import re
from collections import Counter
from difflib import SequenceMatcher

import duckdb
import mwparserfromhell

from . import config
from .corpus import Corpus

_S = config.session()

STANCE_VAL = {"critical": -1, "neutral": 0, "absent": 0, "sympathetic": 1}
STANCE_PROMPT_VERSION = "stance-v3"
STANCE_SCHEMA_VERSION = 1
DEFAULT_REPEATED_RUNS = 3
DEFAULT_AGREEMENT_FLOOR = 0.8
DEFAULT_EVIDENCE_COVERAGE_FLOOR = 1.0
DEFAULT_TEXT_SIMILARITY_FLOOR = 0.98

SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "stance": {"type": "string", "enum": ["critical", "neutral", "sympathetic", "absent"]},
                    "npov_departure": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string"},
                    "evidence_spans": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["entity", "stance", "npov_departure", "confidence", "evidence", "evidence_spans"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entities"],
    "additionalProperties": False,
}

PROMPT = """You are auditing Wikipedia prose for NEUTRALITY (NPOV), not sentiment. For each focal entity,
judge how THIS passage frames it, on an encyclopedic-neutrality axis:
  - "critical": the passage leans toward portraying the entity negatively / as culpable, beyond neutral description.
  - "sympathetic": the passage leans toward portraying the entity positively / as victim or justified, beyond neutral description.
  - "neutral": described in even, encyclopedic terms.
  - "absent": the entity is not meaningfully discussed here.
Set npov_departure=true only if the framing departs from neutral encyclopedic tone toward a viewpoint.
Give a calibrated confidence from 0 to 1 and short verbatim evidence spans from this passage. Also put
the primary span in evidence for backward compatibility. This is a LEAD for a human, not a verdict — a real-world event can
legitimately reshape framing. Focal entities: {entities}

PASSAGE:
{passage}"""


def summarize_entity_runs(receipt, entity):
    """Summarize repeated raw labels for one entity without discarding disagreement."""
    records = [
        record
        for run in receipt.get("runs", [])
        for record in run.get("entities", [])
        if record.get("entity") == entity
    ]
    if not records:
        return None
    counts = Counter(record.get("stance", "absent") for record in records)
    label, count = counts.most_common(1)[0]
    evidence_records = [record for record in records if record.get("evidence_spans") or record.get("evidence")]
    confidences = [record["confidence"] for record in records if isinstance(record.get("confidence"), (int, float))]
    return {
        "label": label,
        "agreement": round(count / len(records), 4),
        "runs": len(records),
        "label_counts": dict(sorted(counts.items())),
        "evidence_coverage": round(len(evidence_records) / len(records), 4),
        "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
    }


def audit_transition(before, after, entity, agreement_floor=DEFAULT_AGREEMENT_FLOOR,
                     text_similarity_floor=DEFAULT_TEXT_SIMILARITY_FLOOR,
                     evidence_coverage_floor=DEFAULT_EVIDENCE_COVERAGE_FLOOR):
    """Separate an observed text change from repeated-run model disagreement."""
    before_summary = summarize_entity_runs(before, entity)
    after_summary = summarize_entity_runs(after, entity)
    if before_summary is None or after_summary is None:
        return {
            "state": "insufficient_evidence",
            "entity": entity,
            "audited_shift": False,
            "reason": "one or both passages have no classification for the entity",
        }
    before_contracts = _run_contracts(before)
    after_contracts = _run_contracts(after)
    if (
        len(before_contracts) > 1
        or len(after_contracts) > 1
        or (before_contracts and after_contracts and before_contracts != after_contracts)
    ):
        return {
            "state": "insufficient_evidence",
            "entity": entity,
            "audited_shift": False,
            "reason": "prompt or model run contracts are incompatible",
            "before_contracts": sorted(before_contracts),
            "after_contracts": sorted(after_contracts),
        }
    text_similarity = SequenceMatcher(
        None, before.get("passage", ""), after.get("passage", "")
    ).ratio()
    text_changed = text_similarity < text_similarity_floor
    model_unstable = (
        before_summary["agreement"] < agreement_floor
        or after_summary["agreement"] < agreement_floor
    )
    evidence_complete = (
        before_summary["evidence_coverage"] >= evidence_coverage_floor
        and after_summary["evidence_coverage"] >= evidence_coverage_floor
    )
    label_changed = before_summary["label"] != after_summary["label"]
    if not evidence_complete:
        state = "insufficient_evidence"
    elif text_changed and model_unstable:
        state = "both_changed"
    elif model_unstable or (label_changed and not text_changed):
        state = "model_unstable"
    elif text_changed:
        state = "text_changed"
    else:
        state = "no_change"
    return {
        "state": state,
        "entity": entity,
        "text_similarity": round(text_similarity, 4),
        "text_changed": text_changed,
        "label_changed": label_changed,
        "before_label": before_summary["label"],
        "after_label": after_summary["label"],
        "before_agreement": before_summary["agreement"],
        "after_agreement": after_summary["agreement"],
        "before_evidence_coverage": before_summary["evidence_coverage"],
        "after_evidence_coverage": after_summary["evidence_coverage"],
        "agreement_floor": agreement_floor,
        "evidence_coverage_floor": evidence_coverage_floor,
        "text_similarity_floor": text_similarity_floor,
        "audited_shift": bool(text_changed and label_changed and not model_unstable and evidence_complete),
    }


def _run_contracts(receipt):
    return {
        (run.get("prompt_version"), run.get("provider"), run.get("model"))
        for run in receipt.get("runs", [])
        if any(run.get(field) is not None for field in ("prompt_version", "provider", "model"))
    }


def build_stance_trajectory(article, entities, revision_passages, client,
                            repeated_runs=DEFAULT_REPEATED_RUNS, run_timestamp=None):
    """Classify exact revision passages and repeat calls only around apparent transitions."""
    if repeated_runs < 1:
        raise ValueError("repeated_runs must be at least 1")
    timestamp_factory = run_timestamp or _utc_now
    receipts = [
        _initial_receipt(article, entities, revision_passage, client, timestamp_factory)
        for revision_passage in revision_passages
    ]
    transition_indexes = set()
    for index, (before, after) in enumerate(zip(receipts, receipts[1:])):
        if any(
            before_label is not None and after_label is not None and before_label != after_label
            for entity in entities
            for before_label, after_label in [
                (_initial_label(before, entity), _initial_label(after, entity))
            ]
        ):
            transition_indexes.update({index, index + 1})
    for index in sorted(transition_indexes):
        while len(receipts[index]["runs"]) < repeated_runs:
            receipts[index]["runs"].append(
                _classification_run(client, entities, receipts[index]["passage"], timestamp_factory)
            )
    for receipt in receipts:
        for run_index, run in enumerate(receipt["runs"], start=1):
            run["run_index"] = run_index
        receipt["summaries"] = {
            entity: summary
            for entity in entities
            if (summary := summarize_entity_runs(receipt, entity)) is not None
        }
    transitions = []
    for before, after in zip(receipts, receipts[1:]):
        transitions.append({
            "before_revision_id": before["revision_id"],
            "after_revision_id": after["revision_id"],
            "entities": {
                entity: audit_transition(before, after, entity)
                for entity in entities
            },
        })
    return {
        "schema_version": STANCE_SCHEMA_VERSION,
        "prompt_version": STANCE_PROMPT_VERSION,
        "article": article,
        "entities": entities,
        "repeated_run_policy": {
            "runs_near_apparent_transition": repeated_runs,
            "agreement_floor": DEFAULT_AGREEMENT_FLOOR,
            "text_similarity_floor": DEFAULT_TEXT_SIMILARITY_FLOOR,
        },
        "revisions": receipts,
        "transitions": transitions,
    }


def _initial_receipt(article, entities, revision_passage, client, timestamp_factory):
    passage = revision_passage.get("passage", "")
    revision_id = int(revision_passage["revision_id"])
    return {
        "schema_version": STANCE_SCHEMA_VERSION,
        "prompt_version": STANCE_PROMPT_VERSION,
        "article": article,
        "revision_id": revision_id,
        "timestamp": revision_passage.get("timestamp"),
        "oldid_url": (
            f"https://en.wikipedia.org/w/index.php?title="
            f"{article.replace(' ', '_')}&oldid={revision_id}"
        ),
        "passage_hash": f"sha256:{hashlib.sha256(passage.encode('utf-8')).hexdigest()}",
        "passage": passage,
        "has_focal_prose": bool(passage),
        "entities": entities,
        "runs": [
            _classification_run(client, entities, passage, timestamp_factory)
        ] if passage else [],
    }


def _classification_run(client, entities, passage, timestamp_factory):
    records = [_normalize_classification(record) for record in classify(client, entities, passage)]
    return {
        "run_index": None,
        "run_timestamp": timestamp_factory(),
        "provider": getattr(client, "provider", None),
        "model": getattr(client, "model", None),
        "prompt_version": STANCE_PROMPT_VERSION,
        "entities": records,
    }


def _normalize_classification(record):
    normalized = dict(record)
    evidence = str(normalized.get("evidence") or "")
    normalized.setdefault("confidence", None)
    normalized.setdefault("evidence_spans", [evidence] if evidence else [])
    return normalized


def _initial_label(receipt, entity):
    runs = receipt.get("runs") or []
    if not runs:
        return None
    for record in runs[0].get("entities", []):
        if record.get("entity") == entity:
            return record.get("stance")
    return None


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def prose_at(rev_id):
    d = _S.get(config.ACTION, params={"action": "query", "format": "json", "formatversion": "2",
              "prop": "revisions", "revids": rev_id, "rvprop": "content", "rvslots": "main"}, timeout=30).json()
    try:
        txt = d["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    except (KeyError, IndexError):
        return ""
    return mwparserfromhell.parse(txt).strip_code()


def focal_passage(prose, entities, max_chars=6000):
    if not entities:
        return ""
    pat = re.compile("|".join(re.escape(e) for e in entities), re.I)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", prose) if pat.search(s)]
    out = " ".join(sents)
    return out[:max_chars]


def classify(client, entities, passage):
    return client.complete_json(
        SCHEMA, PROMPT.format(entities=", ".join(entities), passage=passage), max_tokens=1024)["entities"]


def default_entities(article):
    """Self-determined default entity focus: the article title itself."""
    title = (article or "").strip()
    return [title] if title else []


def stance_over_time(article, entities=None, max_snaps=0, since=None, provider=None, model=None, base_url=None,
                     client=None, repeated_runs=DEFAULT_REPEATED_RUNS, persist=True):
    """Classify focal-entity stance across the article's snapshots and report the directional shift.

    `client` is the LLM port (dependency-injected). When None it is built from provider/model/base_url, so
    CLI callers are unaffected; the pipeline injects ONE shared client across L2+L5. `since` (ISO date)
    restricts to snapshots on/after that date — use it to TARGET the L1 pivot window (with a pre-pivot
    baseline) rather than even-sampling, which under-weights a late pivot (S07: Zionism's 4-sample even-sample
    stopped at 2020 and missed its 2024-26 pivot)."""
    entities = entities or default_entities(article)
    con = duckdb.connect(str(config.DB), read_only=True)
    snaps = Corpus(con).snapshots(article)
    con.close()
    if since:                                               # target the pivot window (keep a pre-pivot baseline)
        snaps = [(sd, sr) for sd, sr in snaps if sd >= since]
    if max_snaps and len(snaps) > max_snaps:                # evenly sample to bound API calls
        step = len(snaps) / max_snaps
        snaps = [snaps[int(i * step)] for i in range(max_snaps)]
    if client is None:
        from . import llm  # imported lazily so offline commands need no LLM SDK / API key
        client = llm.make_client(provider, model, base_url)
    print(f"=== L2 STANCE over time — {article} ===\nfocal entities: {entities}\n")
    header = "date        | " + " | ".join(f"{e[:14]:>14}" for e in entities)
    print(header + "\n" + "-" * len(header))
    revision_passages = []
    for sd, sr in snaps:
        prose = prose_at(sr)
        passage = focal_passage(prose, entities)
        if not passage:
            print(f"{sd} | (no focal prose)")
        revision_passages.append({
            "revision_id": sr,
            "timestamp": str(sd),
            "passage": passage,
        })
    audit = build_stance_trajectory(
        article, entities, revision_passages, client, repeated_runs=repeated_runs
    )
    for revision in audit["revisions"]:
        summaries = revision.get("summaries") or {}
        cells = []
        for entity in entities:
            summary = summaries.get(entity)
            if not summary:
                cells.append(f"{'absent':>12}  ")
                continue
            agreement = summary["agreement"]
            cells.append(f"{summary['label'][:9]:>9} {agreement:>4.2f} ")
        print(f"{revision['timestamp']} | " + "| ".join(cells))

    print("\n── audited transitions (text change separated from model agreement) ──")
    shifts = {}
    for entity in entities:
        summaries = [
            revision["summaries"][entity]
            for revision in audit["revisions"]
            if entity in revision.get("summaries", {})
        ]
        if not summaries:
            continue
        entity_transitions = [
            transition["entities"][entity]
            for transition in audit["transitions"]
            if entity in transition["entities"]
        ]
        audited_transitions = [
            transition for transition in entity_transitions if transition.get("audited_shift")
        ]
        start = STANCE_VAL.get(summaries[0]["label"], 0)
        end = STANCE_VAL.get(summaries[-1]["label"], 0)
        shifts[entity] = {
            "start": start,
            "end": end,
            "shifted": bool(audited_transitions),
            "n": len(summaries),
            "audited_transition_count": len(audited_transitions),
            "transition_states": [transition["state"] for transition in entity_transitions],
        }
        if audited_transitions:
            print(f"  {entity}: {len(audited_transitions)} audited text-linked stance transition(s)")
        elif entity_transitions:
            states = ", ".join(transition["state"] for transition in entity_transitions)
            print(f"  {entity}: no audited stance transition ({states})")
    audit["shifts"] = shifts
    audit["since"] = since
    if persist:
        config.write_findings(f"{config.slugify(article)}.stance-trajectory.json", audit)
    return audit

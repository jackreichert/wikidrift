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
import re

import duckdb
import mwparserfromhell

from . import config
from .corpus import Corpus
from .registry import FOCAL, DEFAULT_FOCAL   # focal entities shared with L5 framing (single source)

_S = config.session()

STANCE_VAL = {"critical": -1, "neutral": 0, "absent": 0, "sympathetic": 1}

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
                    "evidence": {"type": "string"},
                },
                "required": ["entity", "stance", "npov_departure", "evidence"],
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
Give a short evidence quote. This is a LEAD for a human, not a verdict — a real-world event can
legitimately reshape framing. Focal entities: {entities}

PASSAGE:
{passage}"""


def prose_at(rev_id):
    d = _S.get(config.ACTION, params={"action": "query", "format": "json", "formatversion": "2",
              "prop": "revisions", "revids": rev_id, "rvprop": "content", "rvslots": "main"}, timeout=30).json()
    try:
        txt = d["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    except (KeyError, IndexError):
        return ""
    return mwparserfromhell.parse(txt).strip_code()


def focal_passage(prose, entities, max_chars=6000):
    pat = re.compile("|".join(re.escape(e) for e in entities), re.I)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", prose) if pat.search(s)]
    out = " ".join(sents)
    return out[:max_chars]


def classify(client, entities, passage):
    return client.complete_json(
        SCHEMA, PROMPT.format(entities=", ".join(entities), passage=passage), max_tokens=1024)["entities"]


def stance_over_time(article, entities=None, max_snaps=0, since=None, provider=None, model=None, base_url=None,
                     client=None):
    """Classify focal-entity stance across the article's snapshots and report the directional shift.

    `client` is the LLM port (dependency-injected). When None it is built from provider/model/base_url, so
    CLI callers are unaffected; the pipeline injects ONE shared client across L2+L5. `since` (ISO date)
    restricts to snapshots on/after that date — use it to TARGET the L1 pivot window (with a pre-pivot
    baseline) rather than even-sampling, which under-weights a late pivot (S07: Zionism's 4-sample even-sample
    stopped at 2020 and missed its 2024-26 pivot)."""
    entities = entities or FOCAL.get(article, DEFAULT_FOCAL)
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
    traj = {e: [] for e in entities}
    for sd, sr in snaps:
        prose = prose_at(sr)
        passage = focal_passage(prose, entities)
        if not passage:
            print(f"{sd} | (no focal prose)"); continue
        rows = {r["entity"]: r for r in classify(client, entities, passage)}
        cells = []
        for e in entities:
            r = rows.get(e, {"stance": "absent", "npov_departure": False})
            val = STANCE_VAL.get(r["stance"], 0)
            traj[e].append(val)
            mark = "!" if r.get("npov_departure") else " "
            cells.append(f"{r['stance'][:12]:>12}{mark} ")
        print(f"{sd} | " + "| ".join(cells))
    print("\n── directional shift (stance start → end; SIGNAL, not proof) ──")
    for e in entities:
        v = traj[e]
        if len(v) >= 2 and v[0] != v[-1]:
            print(f"  {e}: {v[0]:+d} → {v[-1]:+d}  ⇒ framing shifted (lead for a researcher + L5)")
        elif v:
            print(f"  {e}: flat ({v[0]:+d}) — no directional shift detected")

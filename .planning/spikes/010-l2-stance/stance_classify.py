"""Spike 010 (L2 production) — LLM stance classifier over time.

Turns "reframed" from a lead into a MEASURED signal — the production path the methodology named
(VADER prototype in 006 was too weak; Johnson 2025 warns sentiment conflates tone with viewpoint
balance). So this classifies stance on an ENCYCLOPEDIC-NEUTRALITY axis (critical / neutral /
sympathetic toward a focal entity + an NPOV-departure flag), NOT sentiment.

It is the discriminator the ★#3 benchmark says L1 lacks: a benign large change (Climate's
restructuring) shows NO directional stance shift; a capture (Zionism/Nakba reframe) does.

Per snapshot (rsnap revs from spike 005): fetch wikitext via the Action API, strip to prose,
keep focal-entity sentences, classify with Claude (structured output). Emit the stance trajectory
and flag a directional shift. SIGNAL, not proof — a real event can legitimately reshape framing.

Usage: uv run --with anthropic python stance_classify.py "Nakba"
       uv run --with anthropic python stance_classify.py "Nakba" --entities "Israel,Palestinians,Zionism" --max-snaps 4
"""
import sys
import re
import json
import pathlib
import requests
import duckdb
import mwparserfromhell
import anthropic

UA = "gh-wiki-spike/0.1 (awesome@rpophesagr.com)"
ACTION = "https://en.wikipedia.org/w/api.php"
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"
MODEL = "claude-opus-4-8"
S = requests.Session(); S.headers.update({"User-Agent": UA})

# Focal entities per article (transparent, editable — the researcher picks who to watch, like 006's
# framing lexicon). A benign-rewrite control (Climate) should show a FLAT trajectory for its entities.
FOCAL = {
    "Nakba": ["Israel", "Zionism", "Palestinians"],
    "Zionism": ["Zionism", "Palestinians", "Israel"],
    "Climate change": ["fossil fuel industry", "climate scientists", "governments"],
}

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
    d = S.get(ACTION, params={"action": "query", "format": "json", "formatversion": "2",
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
    resp = client.messages.create(
        model=MODEL, max_tokens=1024,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(entities=", ".join(entities), passage=passage)}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)["entities"]


def main(article, entities, max_snaps):
    con = duckdb.connect(str(DB), read_only=True)
    snaps = con.execute("SELECT DISTINCT snap_date, snap_rev FROM rsnap WHERE article=? ORDER BY snap_date",
                        [article]).fetchall()
    con.close()
    if max_snaps and len(snaps) > max_snaps:                # evenly sample to bound API calls
        step = len(snaps) / max_snaps
        snaps = [snaps[int(i * step)] for i in range(max_snaps)]
    client = anthropic.Anthropic()
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


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    article = args[0] if args else "Nakba"
    ents = FOCAL.get(article, ["Israel", "Palestinians"])
    maxn = 0
    for i, a in enumerate(sys.argv):
        if a == "--entities" and i + 1 < len(sys.argv):
            ents = [e.strip() for e in sys.argv[i + 1].split(",")]
        if a == "--max-snaps" and i + 1 < len(sys.argv):
            maxn = int(sys.argv[i + 1])
    main(article, ents, maxn)

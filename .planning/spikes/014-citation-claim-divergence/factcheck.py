"""Spike 014 — cross-edition citation + claim divergence (L5 instrument #2).

Closes the gap instrument #1 (cross-lingual STANCE, spike 012) can't: *factual/numerical*
distortion (KL Warschau's victim-count myth reads flat to stance). Two signals, both
comparing the SAME article across language editions:

  CITATION divergence — extract each edition's cited domains (external links / refs) and
      measure overlap (Jaccard). Low overlap ⇒ editions rest on disjoint evidence bases.
      Pure parsing, no LLM.

  CLAIM divergence — for a few load-bearing factual questions, extract each edition's answer
      (Claude, structured, native text), then adjudicate across editions: agree / differ /
      CONTRADICT (incompatible facts). Contradiction on a number or category is the catch.

Supports an as-of timestamp, so we can check KL Warschau BEFORE its documented correction —
the temporal analogue of instrument #1's pivot-relative mode.

Contract: a CONTRADICT verdict is a LEAD for a researcher, never a published verdict.
Needs ANTHROPIC_API_KEY. Run:  .venv/bin/python .planning/spikes/014-citation-claim-divergence/factcheck.py
"""
import json
import pathlib
import sys
import urllib.parse

import requests
import mwparserfromhell

ROOT = pathlib.Path(__file__).resolve().parents[3]
BASE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wikidrift import config          # noqa: E402
import anthropic                      # noqa: E402

A012A = BASE / "012a-crosslingual-align" / "out"
OUT = pathlib.Path(__file__).resolve().parent / "out"
UA = "gh-wiki/0.1 (awesome@rpophesagr.com; wikipedia-drift-detector L5 crosslingual spike)"
_S = requests.Session()
_S.headers.update({"User-Agent": UA})

# Load-bearing factual questions per article (transparent — the researcher picks them).
QUESTIONS = {
    "Warsaw concentration camp": [
        "What is the estimated total number of people killed at this camp?",
        "Who were the primary victims (e.g. Jews, ethnic Poles, Soviet POWs)?",
        "Was it primarily an extermination/death camp, or a concentration/labor camp?",
    ],
    "Photosynthesis": [
        "What are the inputs and outputs of the overall photosynthesis reaction?",
        "In which cellular structure does photosynthesis take place?",
    ],
    "Nakba": [
        "Approximately how many Palestinians were displaced?",
        "Is the event described as ethnic cleansing?",
    ],
    # Framing case (complement to Nakba): the divergence is in framing (instrument #1);
    # these load-bearing *factual* anchors are expected to AGREE across editions.
    "Zionism": [
        "In what year and city was the First Zionist Congress held, and who convened it?",
        "In what geographic region did the Zionist movement seek to establish a Jewish homeland?",
        "Approximately when did the modern political Zionist movement emerge?",
    ],
}

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answers"],
    "properties": {"answers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["question", "answer", "value", "evidence"],
        "properties": {"question": {"type": "string"}, "answer": {"type": "string"},
                       "value": {"type": "string"}, "evidence": {"type": "string"}}}}},
}
ADJ_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["questions"],
    "properties": {"questions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["question", "verdict", "note"],
        "properties": {"question": {"type": "string"},
                       "verdict": {"type": "string", "enum": ["agree", "differ", "contradict", "insufficient"]},
                       "note": {"type": "string"}}}}},
}
EXTRACT_PROMPT = """Answer each question STRICTLY from this Wikipedia passage (it may be non-English).
For each: a short 'answer', a normalized 'value' (a number/range/short category, or "not stated"),
and a short 'evidence' quote. Questions:
{qs}

PASSAGE:
{passage}"""
ADJ_PROMPT = """Below are answers to the same questions, each from a different-language Wikipedia edition
of the SAME article. For each question judge ACROSS editions:
  - "agree": consistent facts.
  - "differ": different detail/emphasis, not incompatible.
  - "contradict": assert INCOMPATIBLE facts (e.g. different numbers or categories).
  - "insufficient": not enough stated to tell.
Add a one-line 'note' naming the divergence. This is a LEAD for a researcher, not a verdict.

{payload}"""


def endpoint(lang):
    return f"https://{lang}.wikipedia.org/w/api.php"


def fetch(lang, title, ts=None):
    p = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
         "titles": title, "rvprop": "ids|timestamp|content", "rvslots": "main", "redirects": 1}
    if ts:
        p["rvstart"] = ts
        p["rvlimit"] = 1
    r = _S.get(endpoint(lang), params=p, timeout=30).json()
    try:
        rev = r["query"]["pages"][0]["revisions"][0]
        content = rev["slots"]["main"]["content"]
    except (KeyError, IndexError):
        return None, None, "", ""
    return rev["revid"], rev["timestamp"], content, mwparserfromhell.parse(content).strip_code()


def domains(raw):
    doms = {}
    for link in mwparserfromhell.parse(raw).filter_external_links():
        net = urllib.parse.urlparse(str(link.url)).netloc.lower()
        net = net[4:] if net.startswith("www.") else net
        if net:
            doms[net] = doms.get(net, 0) + 1
    return doms


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if (a | b) else 1.0


def call(client, schema, prompt, max_tokens=1600):
    resp = client.messages.create(model=config.MODEL, max_tokens=max_tokens,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}])
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def run(client, article, langs, receipts, ts=None):
    tag = f" @ {ts[:10]}" if ts else " (now)"
    print(f"\n=== {article}{tag}  ({'/'.join(langs)}) — L5 instrument #2 ===")
    res = {"article": article, "asof": ts, "langs": langs}

    # --- citation divergence ---
    dom = {}
    for l in langs:
        _, _, raw, _ = fetch(l, receipts["editions"][l]["title"], ts)
        dom[l] = domains(raw)
    pairs = [(langs[i], langs[j]) for i in range(len(langs)) for j in range(i + 1, len(langs))]
    js = [jaccard(dom[a], dom[b]) for a, b in pairs] or [1.0]
    mean_j = sum(js) / len(js)
    print(f"  CITATION: mean pairwise domain overlap (Jaccard) = {mean_j:.2f}  (1=identical sources, 0=disjoint)")
    for l in langs:
        top = sorted(dom[l].items(), key=lambda kv: -kv[1])[:4]
        print(f"    {l}: {len(dom[l])} domains  top: {', '.join(d for d, _ in top)}")
    res["citation"] = {"mean_jaccard": round(mean_j, 2), "domains_per_edition": {l: len(dom[l]) for l in langs}}

    # --- claim divergence ---
    qs = QUESTIONS.get(article, [])
    per = {}
    for l in langs:
        _, _, _, prose = fetch(l, receipts["editions"][l]["title"], ts)
        ans = call(client, EXTRACT_SCHEMA,
                   EXTRACT_PROMPT.format(qs="\n".join(f"- {q}" for q in qs), passage=prose[:8000]))["answers"]
        per[l] = {a["question"]: a for a in ans}
    lines = []
    for q in qs:
        lines.append(f"Q: {q}")
        for l in langs:
            a = per[l].get(q, {})
            lines.append(f"  [{l}] value={a.get('value', '?')!r} — {a.get('answer', '')[:180]}")
    adj = call(client, ADJ_SCHEMA, ADJ_PROMPT.format(payload="\n".join(lines)))["questions"]
    print("  CLAIM divergence:")
    for a in adj:
        mark = "‼" if a["verdict"] == "contradict" else (" " if a["verdict"] == "agree" else "·")
        print(f"    [{a['verdict']:>11}]{mark} {a['question'][:60]}")
        print(f"        {a['note'][:110]}")
    res["claim"] = {"per_edition": per, "adjudication": adj}

    OUT.mkdir(exist_ok=True)
    slug = article.replace(" ", "_") + (f".asof-{ts[:10]}" if ts else "")
    (OUT / f"{slug}.factcheck.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> out/{slug}.factcheck.json")
    return res


if __name__ == "__main__":
    client = anthropic.Anthropic()
    def receipts_for(article):
        slug = article.replace(" ", "_")
        r = json.loads((A012A / f"{slug}.receipts.json").read_text(encoding="utf-8"))
        return r, [l for l, v in r["editions"].items() if v.get("present")]

    # KL Warschau — the fact-distortion target — now AND before the documented correction.
    r, langs = receipts_for("Warsaw concentration camp")
    run(client, "Warsaw concentration camp", langs, r)
    run(client, "Warsaw concentration camp", langs, r, ts="2018-06-01T00:00:00Z")
    # Control + framing cases for contrast (facts expected to agree; framing diverges).
    r, langs = receipts_for("Photosynthesis"); run(client, "Photosynthesis", langs, r)
    r, langs = receipts_for("Nakba"); run(client, "Nakba", langs, r)
    r, langs = receipts_for("Zionism"); run(client, "Zionism", langs, r)

"""Spike 012b — native-language stance classification (L5 instrument #1, step 2).

Reuses the L2 NPOV classifier (src/wikidrift/stance.py) on 012a's per-edition prose,
WITHOUT translation (prompt in English, article text native). Builds TWO passage-
selection variants and compares them head-to-head:

  A. focal — keep only sentences mentioning the focal entities, matched by their
             NATIVE labels (each entity's own article title per language, via Wikidata).
  B. lead  — the first N chars (lead + early body), no focal filtering.

Question this validates: does native classification produce a coherent, cross-lingually
COMPARABLE stance signal — and which passage-selection strategy is more reliable?

Needs ANTHROPIC_API_KEY. Run:
    .venv/bin/python .planning/spikes/012b-native-stance/native_stance.py            # whole slate
    .venv/bin/python .planning/spikes/012b-native-stance/native_stance.py "Nakba"    # one article
"""
import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))
from wikidrift.stance import classify, focal_passage, STANCE_VAL  # noqa: E402
import anthropic  # noqa: E402

A012A = pathlib.Path(__file__).resolve().parents[1] / "012a-crosslingual-align" / "out"
OUT = pathlib.Path(__file__).resolve().parent / "out"
WIKIDATA = "https://www.wikidata.org/w/api.php"
UA = "gh-wiki/0.1 (awesome@rpophesagr.com; wikipedia-drift-detector L5 crosslingual spike)"
_S = requests.Session()
_S.headers.update({"User-Agent": UA})

# Focal entities per article, as English article titles (the comparison key + Wikidata handle).
FOCAL = {
    "Nakba": ["Israel", "Palestinians", "Zionism"],
    "Zionism": ["Israel", "Palestinians", "Zionism"],
    "Photosynthesis": ["Plant", "Sunlight"],                       # neutral control
    "Warsaw concentration camp": ["Poland", "Germany", "Jews"],    # KL Warschau distortion axis
}
MAX_CHARS = 6000
ABBR = {"critical": "crit", "sympathetic": "symp", "neutral": "neut", "absent": "abs "}


def native_labels(entity_title, langs):
    """English entity title -> {lang: native surface form} (the entity's own article title per edition)."""
    r = _S.get(WIKIDATA, params={"action": "wbgetentities", "format": "json", "sites": "enwiki",
               "titles": entity_title, "props": "sitelinks", "normalize": 1}, timeout=30).json()
    ent = next(iter(r["entities"].values()))
    out = {"en": entity_title}
    for lang in langs:
        sl = ent.get("sitelinks", {}).get(f"{lang}wiki")
        if sl:
            out[lang] = sl["title"]
    return out


def cell(rec):
    """Format a classifier record as e.g. 'crit!' (! = npov_departure)."""
    if not rec:
        return "----"
    return f"{ABBR.get(rec['stance'], rec['stance'][:4])}{'!' if rec.get('npov_departure') else ' '}"


def agree(recs):
    """Do all editions agree on stance SIGN for this entity? recs: {lang: record}."""
    signs = {STANCE_VAL.get(r["stance"], 0) for r in recs.values() if r}
    return len(signs) <= 1


def run_article(article):
    slug = article.replace(" ", "_")
    receipts = json.loads((A012A / f"{slug}.receipts.json").read_text(encoding="utf-8"))
    langs = [l for l, v in receipts["editions"].items() if v.get("present")]
    ents = FOCAL.get(article, ["Israel", "Palestinians"])
    labels = {e: native_labels(e, langs) for e in ents}          # entity -> {lang: native}
    prose = {l: (A012A / receipts["editions"][l]["prose_file"]).read_text(encoding="utf-8") for l in langs}
    client = anthropic.Anthropic()

    print(f"\n=== {article}  ({'/'.join(langs)})  — native stance, no translation ===")
    res = {"article": article, "entities": ents, "langs": langs, "editions": {}}
    for l in langs:
        nl = [labels[e].get(l, e) for e in ents]
        pa = focal_passage(prose[l], nl, MAX_CHARS)
        pa_used = pa if pa else prose[l][:MAX_CHARS]              # fall back to lead if no focal sentences
        pb = prose[l][:MAX_CHARS]
        ra = {r["entity"]: r for r in classify(client, ents, pa_used)}
        time.sleep(0.4)
        rb = {r["entity"]: r for r in classify(client, ents, pb)}
        time.sleep(0.4)
        res["editions"][l] = {"native_labels": dict(zip(ents, nl)),
                              "focal_empty": not bool(pa), "focal": ra, "lead": rb}

    # per-variant table + agreement
    for variant in ("focal", "lead"):
        print(f"\n  [{variant}]  entity        | " + " | ".join(f"{l:>6}" for l in langs) + " | editions")
        for e in ents:
            recs = {l: res["editions"][l][variant].get(e) for l in langs}
            row = " | ".join(f"{cell(recs[l]):>6}" for l in langs)
            print(f"           {e[:14]:>14} | {row} | {'AGREE' if agree(recs) else 'DIVERGE <<'}")
    fe = [l for l in langs if res['editions'][l]['focal_empty']]
    if fe:
        print(f"  note: focal filter found no sentences in {fe} -> fell back to lead there")

    OUT.mkdir(exist_ok=True)
    (OUT / f"{slug}.stance.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> out/{slug}.stance.json")
    return res


if __name__ == "__main__":
    targets = [sys.argv[1]] if len(sys.argv) > 1 else list(FOCAL)
    for a in targets:
        run_article(a)

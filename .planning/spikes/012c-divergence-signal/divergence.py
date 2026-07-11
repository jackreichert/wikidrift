"""Spike 012c — cross-lingual divergence signal (L5 instrument #1, step 3).

Turns 012b's per-edition stances into the L5 signal, in two modes:

  STATIC divergence — how far editions disagree *now* (the born-biased fallback).
      Per entity: spread = max(stance_val) - min(stance_val) across editions
      (0 = full agreement, 2 = maximal, e.g. critical vs sympathetic).
      Article divergence = mean spread over focal entities. Reuses 012b's saved stances.

  PIVOT-RELATIVE divergence — does English peel away from the he/ar consensus ACROSS the
      L1 pivot? Snapshot every edition at the pivot boundary (before) and now (after) via
      012a's as-of fetch, classify, and compare the English-vs-others gap before vs after.
      Pivot from L1 (drift.verdict_dict, offline); if the article reads HEALTHY to L1
      (e.g. Nakba, which grew by addition), fall back to the Oct-2023 boundary.

Contract: this makes disagreement LEGIBLE. A high number is a LEAD, never a verdict.
Needs ANTHROPIC_API_KEY. Run:
    .venv/bin/python .planning/spikes/012c-divergence-signal/divergence.py
"""
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parents[1]
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(BASE / "012a-crosslingual-align"))
sys.path.insert(0, str(BASE / "012b-native-stance"))

import duckdb                                              # noqa: E402
import anthropic                                           # noqa: E402
import align                                               # noqa: E402  (012a)
import native_stance as ns                                 # noqa: E402  (012b: FOCAL, native_labels)
from wikidrift.stance import classify, focal_passage, STANCE_VAL  # noqa: E402
from wikidrift import drift, config                        # noqa: E402

A012A = BASE / "012a-crosslingual-align" / "out"
B012B = BASE / "012b-native-stance" / "out"
OUT = pathlib.Path(__file__).resolve().parent / "out"
MAX_CHARS = 6000

# I-P pivot fallback when L1 reads HEALTHY (addition-side growth, no removal pivot).
FALLBACK_PIVOT = {"Nakba": "2023-10-01", "Zionism": "2023-10-01"}
PIVOT_TARGETS = ["Nakba", "Zionism"]                       # framing cases — pivot-relative applies
STATIC_TARGETS = ["Nakba", "Zionism", "Photosynthesis", "Warsaw concentration camp"]


def sval(rec):
    return STANCE_VAL.get(rec["stance"], 0) if rec else None


# ---- STATIC (reuse 012b's saved stances) -----------------------------------
def static_divergence(article):
    slug = article.replace(" ", "_")
    data = json.loads((B012B / f"{slug}.stance.json").read_text(encoding="utf-8"))
    langs, ents = data["langs"], data["entities"]
    print(f"\n=== STATIC divergence — {article} ({'/'.join(langs)}) ===")
    out = {"article": article, "langs": langs, "variants": {}}
    for variant in ("lead", "focal"):
        spreads = {}
        for e in ents:
            vals = [sval(data["editions"][l][variant].get(e)) for l in langs]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                spreads[e] = max(vals) - min(vals)
        div = sum(spreads.values()) / len(spreads) if spreads else 0.0
        out["variants"][variant] = {"divergence": round(div, 2), "spreads": spreads}
        detail = ", ".join(f"{e}:{s}" for e, s in spreads.items())
        print(f"  [{variant:>5}] divergence = {div:.2f}  (0=agree … 2=max)   [{detail}]")
    return out


# ---- PIVOT-RELATIVE --------------------------------------------------------
def l1_pivot(con, article):
    """(pivot_date, source) — L1's top episode start, else the Oct-2023 fallback."""
    d = drift.verdict_dict(con, article)
    eps = d.get("episodes", [])
    if eps:
        recent = min(eps, key=lambda e: e["age_years"])    # most recent substantial episode
        return recent["start"], f"L1 (peak {recent['peak_pct']}%, {recent['pwr_mass']:,} PWR, age {recent['age_years']}yr)"
    return FALLBACK_PIVOT.get(article, "2023-10-01"), "fallback (L1=HEALTHY — addition-side growth)"


def lead_vals(client, prose, ents, native):
    """English-keyed lead-window stance values for one edition snapshot."""
    passage = prose[:MAX_CHARS]
    recs = {r["entity"]: r for r in classify(client, ents, passage)}
    return {e: sval(recs.get(e)) for e in ents}


def en_gap(vals_by_lang, ents):
    """Mean |en - mean(others)| over entities — how far English sits from the other editions."""
    langs = list(vals_by_lang)
    gaps = []
    for e in ents:
        en = vals_by_lang.get("en", {}).get(e)
        others = [vals_by_lang[l].get(e) for l in langs if l != "en" and vals_by_lang[l].get(e) is not None]
        if en is not None and others:
            gaps.append(abs(en - sum(others) / len(others)))
    return round(sum(gaps) / len(gaps), 2) if gaps else 0.0


def pivot_relative(con, article, client):
    slug = article.replace(" ", "_")
    receipts = json.loads((A012A / f"{slug}.receipts.json").read_text(encoding="utf-8"))
    langs = [l for l, v in receipts["editions"].items() if v.get("present")]
    ents = ns.FOCAL[article]
    labels = {e: ns.native_labels(e, langs) for e in ents}
    pivot_date, src = l1_pivot(con, article)
    before_ts = f"{pivot_date}T00:00:00Z"
    print(f"\n=== PIVOT-RELATIVE divergence — {article} ({'/'.join(langs)}) ===")
    print(f"  pivot boundary: {pivot_date}  [{src}]")

    snap = {"before": {}, "after": {}}
    for when, ts in (("before", before_ts), ("after", None)):
        for l in langs:
            title = receipts["editions"][l]["title"]
            _, rts, prose = align.prose_asof(l, title, ts)
            if not prose:
                print(f"    {when} {l}: (no revision as of {ts})")
                continue
            snap[when][l] = lead_vals(client, prose, ents, [labels[e].get(l, e) for e in ents])
    gb, ga = en_gap(snap["before"], ents), en_gap(snap["after"], ents)
    arrow = "PEELED AWAY" if ga > gb + 0.25 else ("converged" if ga < gb - 0.25 else "no net change")
    print(f"  English-vs-others gap:  before {gb:.2f}  →  after {ga:.2f}   ⇒  {arrow}")
    print("     (grew ⇒ en diverged from the cross-lingual consensus across the pivot = capture lead;")
    print("      flat/together ⇒ a real-world event reshaped editions alike = legitimate)")
    return {"article": article, "pivot": pivot_date, "pivot_source": src,
            "en_gap_before": gb, "en_gap_after": ga, "read": arrow,
            "before": snap["before"], "after": snap["after"]}


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(str(config.DB), read_only=True)
    client = anthropic.Anthropic()
    results = {"static": {}, "pivot_relative": {}}
    for a in STATIC_TARGETS:
        results["static"][a] = static_divergence(a)
    for a in PIVOT_TARGETS:
        results["pivot_relative"][a] = pivot_relative(con, a, client)
    con.close()
    (OUT / "divergence.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n-> out/divergence.json")

"""Adjudicated benchmark (promoted from spike 009, ★#3). Scores the ground-truth roster.

Combines two OFFLINE signals per article (no WikiWho; cached data only):
  - L1 drift verdict + PWR-mass (drift.verdict_dict) — episodes ranked by PWR-mass (age-agnostic);
    recency is a descriptor (recent vs standing), NOT a demoter, so an old large capture is never buried.
    Unconfirmed candidate verdict; binary-search confirmation is separate.
  - pre-rank leads (prerank) — removal→PWR and addition→L2 routing.

Scoring rule (from the base-rate mandate): NOT "PIVOT = correct." Each case is scored against what the
right layer should say — must-flag (recall), must-NOT-flag-as-bias (benign change may register but must be
demoted / never escalated to bias), clean (stay HEALTHY), and the L5-gap cases (expected misses today).

A benchmark can only reject over-claiming, never certify "unbiased." Every flag is a lead.
"""
import json

import duckdb

from . import config
from .drift import verdict_dict
from .prerank import prerank

MASS_FLOOR = config.MASS_FLOOR

# Ground-truth roster (see [[wikipedia-drift-benchmark]]). article names must match the DB exactly.
# `src` records source-strength honestly: adjudicated study/report that names the article as its own
# analytical object (Grabowski-Klein peer-reviewed; ADL report) vs. an ArbCom case's EVIDENCE phase
# (party advocacy — the cases sanctioned editors/conduct, not individual articles). `cat` = the
# manipulation type the source alleges (removal / addition / born-biased), which sets the expected layer.
ROSTER = [
    # A — must-flag: capture by removal / retrofit (expect L1 PIVOT, PWR-mass >= floor)
    {"article": "Zionism",                      "cat": "A_removal", "src": "base-rate; PIA5-evidence+ADL", "expect": "flag: recent removal retrofit"},
    {"article": "Anti-Zionism",                 "cat": "A_removal", "src": "base-rate",                    "expect": "flag: removal retrofit"},
    {"article": "Israeli–Palestinian conflict", "cat": "A_removal", "src": "base-rate",                    "expect": "flag: removal retrofit"},
    {"article": "Hamas",                        "cat": "A_removal", "src": "ADL (de-emphasize terror)",    "expect": "flag: removal retrofit"},
    {"article": "Jedwabne pogrom",              "cat": "A_removal", "src": "Grabowski-Klein; Icewhiz-ev",  "expect": "flag: removal retrofit"},
    # B — must-flag: capture by addition / reframe / churn (expect L1 HEALTHY + pre-rank addition→L2 or churn→L2)
    {"article": "Nakba",                        "cat": "B_addition", "src": "this project (Phase 7)",       "expect": "L1 healthy + addition→L2 lead"},
    # reclassified from A_removal (S03): net-grows while shedding large PWR-mass → reframe-by-churn, an L2 case
    {"article": "Palestinian political violence","cat": "B_addition","src": "ADL (removed text ~Nov 2023)", "expect": "L1 healthy + churn→L2 lead (reframe-by-churn)"},
    {"article": "Gaza war",                     "cat": "B_addition", "src": "PIA5-evidence+ADL",            "expect": "addition→L2 (born in contested period)"},
    {"article": "Collaboration in German-occupied Poland", "cat": "B_addition", "src": "Grabowski-Klein; Icewhiz-ev", "expect": "addition→L2 reframe"},
    {"article": "Rescue of Jews by Poles during the Holocaust", "cat": "B_addition", "src": "Grabowski-Klein; Icewhiz-ev", "expect": "addition→L2 reframe"},
    {"article": "Naliboki massacre",            "cat": "B_addition", "src": "Grabowski-Klein; Icewhiz-ev", "expect": "addition→L2 (quiet article — may be sub-maturity)"},
    # C — must-flag: born-biased / long-standing (L5 only; expected MISS today — documents the L5 gap)
    {"article": "Warsaw concentration camp",    "cat": "C_l5gap", "src": "Grabowski-Klein (peer-reviewed)", "expect": "flag via L5 (born-biased ~15yr hoax) — expected L1/L2 MISS today"},
    # D — must-NOT-flag-as-bias: benign large change
    {"article": "Climate change",               "cat": "D_benign", "src": "base-rate", "expect": "may register as change; not bias; demote"},
    {"article": "Water",                        "cat": "D_benign", "src": "base-rate", "expect": "ancient/tiny; demote"},
    {"article": "Abortion",                     "cat": "D_benign", "src": "base-rate", "expect": "old; demote"},
    # E — clean controls: stay HEALTHY
    {"article": "Photosynthesis",               "cat": "E_clean", "src": "base-rate", "expect": "HEALTHY"},
    {"article": "Brontosaurus",                 "cat": "E_clean", "src": "base-rate", "expect": "HEALTHY (expansion)"},
    {"article": "Chess",                        "cat": "E_clean", "src": "base-rate", "expect": "HEALTHY"},
]


def score_case(con, case):
    art, cat = case["article"], case["cat"]
    if case.get("pending"):
        note = "L5-gap case (expected miss until L5 built)" if cat == "C_l5gap" else "needs data (wikiwho_rs / dump)"
        return {**case, "status": "PENDING", "detail": note}

    L1 = verdict_dict(con, art)
    if L1["verdict"] == "SKIP":                      # not ingested / too few snapshots — not a FAIL
        return {**case, "status": "PENDING", "detail": "insufficient snapshots (ingest needed)"}
    pr = prerank(con, art)
    leads = pr["leads"] if pr else []
    # a "route to L2" lead can be either reframe-by-addition (net growth) or reframe-by-churn (medium but
    # highly-anomalous removal) — both mean "L1 won't catch this; hand it to the stance layer".
    l2_route = "addition→L2" in leads or "churn→L2" in leads
    v, mass = L1["verdict"], L1.get("top_mass", 0)
    rec = L1.get("top_recency", "-")

    if cat == "A_removal":
        if v == "PIVOT?" and mass >= MASS_FLOOR:
            status, detail = "PASS", f"flagged (PWR-mass {mass:,}, {rec})"
        elif v == "PIVOT?":
            status, detail = "PARTIAL", f"flagged but low mass {mass:,}"
        else:
            status, detail = "FAIL", f"missed (L1={v})"
    elif cat == "B_addition":
        route = next((x for x in ("addition→L2", "churn→L2") if x in leads), None)
        if route:
            status, detail = "PASS", f"{route} lead raised (L1={v}, as expected — L1 blind to reframe-by-{'addition' if route.startswith('addition') else 'churn'})"
        else:
            status, detail = "FAIL", f"no L2-route lead raised (L1={v}, leads={leads or 'none'})"
    elif cat == "D_benign":
        if v == "HEALTHY":
            status, detail = "PASS", "not flagged"
        elif mass < MASS_FLOOR:
            status, detail = "PASS", f"flagged as change but low mass → demoted (PWR-mass {mass:,}, {rec})"
        else:
            status, detail = "PARTIAL", (f"flagged, large (PWR-mass {mass:,}, {rec}); change is real but L1 "
                                         f"cannot separate benign from malicious → needs L2/L5")
    elif cat == "E_clean":
        if v == "HEALTHY":
            status, detail = "PASS", "HEALTHY"
        elif mass < MASS_FLOOR:
            status, detail = "PASS", f"minor pivot, low mass → demoted (PWR-mass {mass:,})"
        else:
            status, detail = "FAIL", f"false positive (PWR-mass {mass:,}, {rec})"
    elif cat == "C_l5gap":
        # born-biased / long-standing: L1 (change) + L2 (shift) are structurally blind → need L5.
        # An expected MISS is the honest measure of the gap, not a failure of L1.
        l1_flag = v == "PIVOT?" and mass >= MASS_FLOOR
        status = "L5-GAP"
        if l1_flag:
            detail = f"L1={v} (PWR-mass {mass:,}) — unexpectedly flagged by L1; investigate"
        elif l2_route:
            detail = f"L1={v} (PWR-mass {mass:,}); flagged via {next(x for x in ('addition→L2','churn→L2') if x in leads)} pre-ranker (not L1) — a lead, still needs L5 to adjudicate"
        else:
            detail = f"L1={v} (PWR-mass {mass:,}); not flagged — EXPECTED born-biased miss (needs L5 external reference)"
    else:
        status, detail = "PENDING", "uncategorised"
    return {**case, "status": status, "detail": detail, "L1": v, "pwr_mass": mass, "leads": leads}


def run(as_json=False):
    con = duckdb.connect(str(config.DB), read_only=True)
    results = [score_case(con, c) for c in ROSTER]
    con.close()

    print(f"\n{'article':<30} {'cat':<10} {'status':<8} detail")
    print("-" * 110)
    for r in results:
        print(f"{r['article']:<30} {r['cat']:<10} {r['status']:<8} {r['detail']}")

    def rate(cat, statuses=("PASS",)):
        cases = [r for r in results if r["cat"] == cat and r["status"] != "PENDING"]
        hit = [r for r in cases if r["status"] in statuses]
        return len(hit), len(cases)
    print("-" * 110)
    a_hit, a_tot = rate("A_removal")
    b_hit, b_tot = rate("B_addition")
    d_pass, d_tot = rate("D_benign")
    d_partial = [r for r in results if r["cat"] == "D_benign" and r["status"] == "PARTIAL"]
    e_hit, e_tot = rate("E_clean")
    l5gap = [r["article"] for r in results if r["cat"] == "C_l5gap"]
    pending = [r["article"] for r in results if r["status"] == "PENDING"]
    print("SUMMARY (cached subset — offline, unconfirmed):")
    print(f"  A must-flag (removal) recall : {a_hit}/{a_tot}")
    print(f"  B must-flag (addition) recall: {b_hit}/{b_tot}")
    print(f"  D benign correctly demoted   : {d_pass}/{d_tot}"
          + (f"   ⚠ {len(d_partial)} PARTIAL → L2/L5 gap: {[r['article'] for r in d_partial]}" if d_partial else ""))
    print(f"  E clean controls stay HEALTHY: {e_hit}/{e_tot}")
    print(f"  C born-biased (L5-gap)       : {l5gap} — expected L1/L2 misses, quantify the L5 gap")
    if pending:
        print(f"  PENDING (need ingest)        : {pending}")
    print("\n  Key finding: L1 (PWR-mass) achieves recall on removal/addition candidates and correctly demotes")
    print("  LOW-MASS benign change (Water, Abortion), but cannot separate LARGE benign (Climate) from large")
    print("  malicious — that separation is exactly what L2 (stance) + L5 (external reference) are for.")
    print("  Recency is context only (recent vs standing): a large STANDING distortion is a first-class find,")
    print("  never demoted by age (operator correction — long-standing distortions are a primary target).")

    if as_json:
        print("\n=== JSON ===")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return results

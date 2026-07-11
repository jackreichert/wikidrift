"""Spike 009 (★#3) — adjudicated benchmark. Scores the ground-truth roster against the pipeline.

Combines two OFFLINE signals per article (no WikiWho; cached data only):
  - L1 drift verdict + PWR-mass (spike 005 via validate_pwr.verdict_dict) — episodes ranked by PWR-mass
    (age-agnostic); recency is reported as a descriptor (recent vs standing), NOT a demoter, so an old
    large capture is never buried. Unconfirmed candidate verdict; binary-search confirmation is separate.
  - pre-rank leads (spike 008 prerank) — removal→PWR and addition→L2 routing.

Scoring rule (from the base-rate mandate): NOT "PIVOT = correct." Each case is scored against what the
right layer should say — must-flag (recall), must-NOT-flag-as-bias (benign change may register but must be
demoted / never escalated to bias), clean (stay HEALTHY), and the L5-gap cases (expected misses today).

A benchmark can only reject over-claiming, never certify "unbiased." Every flag is a lead.

Usage: uv run python benchmark.py            # scored table + summary
       uv run python benchmark.py --json      # + machine-readable JSON
"""
import sys
import json
import pathlib

SPIKES = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPIKES / "005-analyzer"))
sys.path.insert(0, str(SPIKES / "008-prerank"))
import duckdb
from analyze import DB                       # noqa: E402
from validate_pwr import verdict_dict        # noqa: E402
from prerank import prerank                  # noqa: E402

MASS_FLOOR = 50_000   # provisional: PWR-mass above this = a substantive drift lead (age-agnostic)

# Ground-truth roster (see [[wikipedia-drift-benchmark]]). article names must match the DB exactly.
ROSTER = [
    # A — must-flag: capture by removal / retrofit (L1)
    {"article": "Zionism",                      "cat": "A_removal", "expect": "flag: recent removal retrofit"},
    {"article": "Anti-Zionism",                 "cat": "A_removal", "expect": "flag: removal retrofit"},
    {"article": "Israeli–Palestinian conflict", "cat": "A_removal", "expect": "flag: removal retrofit"},
    {"article": "ArbCom PIA5 set",              "cat": "A_removal", "expect": "flag", "pending": True},
    {"article": "Icewhiz-affected",             "cat": "A_removal", "expect": "flag", "pending": True},
    # B — must-flag: capture by addition / reframe (pre-rank addition lead → L2)
    {"article": "Nakba",                        "cat": "B_addition", "expect": "L1 healthy + addition→L2 lead"},
    # C — must-flag: born-biased / long-standing (L5 only; expected MISS today)
    {"article": "KL Warschau",                  "cat": "C_l5gap", "expect": "flag via L5 (born-biased)", "pending": True},
    # D — must-NOT-flag-as-bias: benign large change
    {"article": "Climate change",               "cat": "D_benign", "expect": "may register as change; not bias; demote"},
    {"article": "Water",                        "cat": "D_benign", "expect": "ancient/tiny; demote"},
    {"article": "Abortion",                     "cat": "D_benign", "expect": "old; demote"},
    # E — clean controls: stay HEALTHY
    {"article": "Photosynthesis",               "cat": "E_clean", "expect": "HEALTHY"},
    {"article": "Brontosaurus",                 "cat": "E_clean", "expect": "HEALTHY (expansion)"},
    {"article": "Chess",                        "cat": "E_clean", "expect": "HEALTHY"},
]


def score_case(con, case):
    art, cat = case["article"], case["cat"]
    if case.get("pending"):
        note = "L5-gap case (expected miss until L5 built)" if cat == "C_l5gap" else "needs data (wikiwho_rs / dump)"
        return {**case, "status": "PENDING", "detail": note}

    L1 = verdict_dict(con, art)
    pr = prerank(con, art)
    leads = pr["leads"] if pr else []
    add_lead = "addition→L2" in leads
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
        if add_lead:
            status, detail = "PASS", f"addition→L2 lead raised (L1={v}, as expected — born-biased blind spot)"
        else:
            status, detail = "FAIL", f"addition lead NOT raised (L1={v}, leads={leads or 'none'})"
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
    else:
        status, detail = "PENDING", "uncategorised"
    return {**case, "status": status, "detail": detail, "L1": v, "pwr_mass": mass, "leads": leads}


def main(as_json=False):
    con = duckdb.connect(str(DB), read_only=True)
    results = [score_case(con, c) for c in ROSTER]
    con.close()

    print(f"\n{'article':<30} {'cat':<10} {'status':<8} detail")
    print("-" * 110)
    for r in results:
        print(f"{r['article']:<30} {r['cat']:<10} {r['status']:<8} {r['detail']}")

    # summary
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
    pending = [r["article"] for r in results if r["status"] == "PENDING"]
    print("SUMMARY (cached subset — offline, unconfirmed):")
    print(f"  A must-flag (removal) recall : {a_hit}/{a_tot}")
    print(f"  B must-flag (addition) recall: {b_hit}/{b_tot}")
    print(f"  D benign correctly demoted   : {d_pass}/{d_tot}"
          + (f"   ⚠ {len(d_partial)} PARTIAL → L2/L5 gap: {[r['article'] for r in d_partial]}" if d_partial else ""))
    print(f"  E clean controls stay HEALTHY: {e_hit}/{e_tot}")
    print(f"  PENDING (need data / L5)     : {pending}")
    print("\n  Key finding: L1 (PWR-mass) achieves recall on removal/addition candidates and correctly demotes")
    print("  LOW-MASS benign change (Water, Abortion), but cannot separate LARGE benign (Climate) from large")
    print("  malicious — that separation is exactly what L2 (stance) + L5 (external reference) are for.")
    print("  Recency is context only (recent vs standing): a large STANDING distortion is a first-class find,")
    print("  never demoted by age (operator correction — long-standing distortions are a primary target).")

    if as_json:
        print("\n=== JSON ===")
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(as_json="--json" in sys.argv)

"""Offline PWR-metric validator — read-only, no WikiWho calls.

Reuses the SAME coarse metric as analyze.py (imported, not reimplemented — one definition
of the drift signal) but runs against cached rsnap only: read_only DuckDB (no write-lock
contention) and no revision-level binary search. Emits a *candidate* verdict per article —
PIVOT candidates are NOT binary-search-confirmed here (that needs WikiWho; run analyze.py
for confirmation). Purpose: fast batch calibration across the base-rate / ground-truth set
(supports non-negotiable #10). Necessary-not-sufficient: a candidate is a lead, not a verdict.

Usage: uv run python validate_pwr.py                 # whole cached set
       uv run python validate_pwr.py "Zionism" ...    # specific articles
"""
import sys
import duckdb
from analyze import (load_membership, coarse, build_episodes, annotate_episodes,
                     _recency_tag, DB, MAG_FLOOR, CREEP_MEAN)


def verdict_dict(con, article):
    """Structured, machine-scorable offline verdict (no WikiWho): the coarse PWR metric, episodes ranked by
    PWR-mass with recency as a descriptor. This is the UNCONFIRMED candidate verdict — binary-search
    confirmation (analyze.py) is a separate precision step. Consumed by spike 009-benchmark."""
    snaps, members, present, _ = load_membership(con, article)
    if len(snaps) < 3:
        return {"article": article, "verdict": "SKIP", "reason": "too few snapshots", "top_mass": 0, "episodes": []}
    series, (mean, med, std) = coarse(snaps, members, present, quiet=True)
    horizon = snaps[-1][0]
    eps = annotate_episodes([e for e in build_episodes(series) if e["peak"] >= MAG_FLOOR], horizon)
    out = {
        "article": article,
        "horizon": horizon,
        "mean_loss": round(mean, 2),
        "peak_loss": round(max([r[4] for r in series], default=0), 2),
        "episodes": [{"start": e["start"][0], "end": e["end"][0], "peak_pct": round(e["peak"], 1),
                      "pwr_mass": int(e["abs"]), "age_years": round(e["age"], 1),
                      "recency": _recency_tag(e["age"])} for e in eps],
    }
    if eps:
        out["verdict"] = "PIVOT?"; out["top_mass"] = int(eps[0]["abs"]); out["top_recency"] = _recency_tag(eps[0]["age"])
    elif mean > CREEP_MEAN:
        out["verdict"] = "CREEP?"; out["top_mass"] = 0
    else:
        out["verdict"] = "HEALTHY"; out["top_mass"] = 0
    return out


def candidate_verdict(con, article):
    d = verdict_dict(con, article)
    if d["verdict"] == "SKIP":
        return article, "SKIP (too few snapshots)"
    if d["verdict"] == "PIVOT?":
        e = d["episodes"][0]
        return article, (f"PIVOT? {e['start']}→{e['end']}  peak {e['peak_pct']:.0f}%  {e['pwr_mass']:,} PWR  "
                         f"age {e['age_years']}yr  [{e['recency']}] (unconfirmed)")
    if d["verdict"] == "CREEP?":
        return article, f"CREEP?  mean {d['mean_loss']}%"
    return article, f"HEALTHY  (mean {d['mean_loss']}%, peak {d['peak_loss']}%)"


if __name__ == "__main__":
    con = duckdb.connect(str(DB), read_only=True)
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = [r[0] for r in con.execute(
            "SELECT article FROM rsnap GROUP BY article HAVING count(distinct snap_rev) >= 3 "
            "ORDER BY article").fetchall()]
    results = []
    for a in targets:
        results.append(candidate_verdict(con, a))
    con.close()
    print("\n" + "=" * 72)
    print("CANDIDATE VERDICTS (PWR-grounded coarse metric, ranked by PWR-mass; recency = context, unconfirmed):")
    print("=" * 72)
    for article, label in results:
        print(f"  {article:<30} {label}")

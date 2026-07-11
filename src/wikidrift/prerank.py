"""Metadata-only candidate pre-ranker (promoted from spike 008). NO article text, NO WikiWho.

Before spending expensive token-level PWR analysis (drift.py), cheaply pre-rank articles from revision
METADATA alone — the columns Quarry / Wiki Replicas expose in bulk (rev timestamp, byte size, actor).
We cache the equivalent in DuckDB (revisions.ts/user + rev_size.size via the Action API), so this runs
offline. Production would source the same columns in bulk from Quarry / Wiki Replicas / dumps.

Signal: per time-bin byte deltas split into removed vs added bytes. `removed_bytes` (Σ of negative deltas)
is a cheap metadata analog of PWR-mass "spine destroyed" — separating a retrofit (big removals) from pure
expansion (mostly additions). Robustness (from spike 005): raw deltas are dominated by transient vandalism
(blank −30k / restore +30k), so deltas are computed on a ROLLING-MEDIAN-SMOOTHED size series — the metadata
analog of 005's persistent-revision snapshot.

RECALL-oriented pre-filter, not a verdict: rank true candidates high enough to never skip them; false
positives are fine (the PWR engine confirms precision). Necessary, not sufficient — a candidate is a lead.
"""
import statistics
import datetime as dt

import duckdb

from . import config
from .corpus import Corpus
from .config import (BIN_DAYS, MIN_REVS, SMOOTH_K, LEAD_FLOOR, ANOMALY_MIN, GROWTH_RATIO,
                     CHURN_ANOMALY, CHURN_MIN_BYTES)


def _bin_index(ts_iso, t0):
    d = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return (d - t0).days // BIN_DAYS


def _rolling_median(sizes, half=SMOOTH_K):
    """Median-filter the size series so isolated vandalism spikes (blank/restore) don't register as
    content churn; sustained changes survive. Metadata analog of 005's persistent-revision snapshot."""
    n = len(sizes)
    return [statistics.median(sizes[max(0, i - half):min(n, i + half + 1)]) for i in range(n)]


def prerank(con, article):
    rows = Corpus(con).size_series(article)
    if len(rows) < MIN_REVS:
        return None
    t0 = dt.datetime.fromisoformat(rows[0][0].replace("Z", "+00:00"))
    smoothed = _rolling_median([(s or 0) for _, s, _ in rows])
    bins = {}   # idx -> dict(removed, added, revs, users, start, end)
    prev = None
    for (ts, _size, user), size in zip(rows, smoothed):
        delta = size - prev if prev is not None else 0
        prev = size
        i = _bin_index(ts, t0)
        b = bins.setdefault(i, {"removed": 0, "added": 0, "revs": 0, "users": {}, "start": ts, "end": ts})
        if delta < 0:
            b["removed"] += -delta
        else:
            b["added"] += delta
        b["revs"] += 1
        b["users"][user] = b["users"].get(user, 0) + 1
        b["end"] = ts

    def baseline_of(key):
        vals = [b[key] for b in bins.values() if b[key] > 0]
        return statistics.median(vals) if vals else 1

    rem_base, add_base = baseline_of("removed"), baseline_of("added")
    pk = bins[max(bins, key=lambda i: bins[i]["removed"])]       # removal peak (retrofit candidate)
    pa = bins[max(bins, key=lambda i: bins[i]["added"])]         # addition peak (reframe candidate)
    rem_anom = pk["removed"] / rem_base
    add_anom = pa["added"] / add_base
    top_ed, top_n = max(pk["users"].items(), key=lambda x: x[1]) if pk["users"] else ("?", 0)

    leads = []
    if pk["removed"] >= LEAD_FLOOR and rem_anom >= ANOMALY_MIN:
        leads.append("removal→PWR")                              # retrofit candidate → token engine
    # Addition lead: a large, net-growth burst (added >> removed) — the reframe-by-addition vector the
    # removal metric is BLIND to (e.g. Nakba's post-Oct-7 expansion). Route to L2 (stance), never dismiss
    # as "growth"; a large sourced expansion can still reframe.
    if pa["added"] >= LEAD_FLOOR and add_anom >= ANOMALY_MIN and pa["added"] > GROWTH_RATIO * pa["removed"]:
        leads.append("addition→L2")
    # Relative-anomaly churn lead: a removal that is small in absolute bytes but hugely anomalous vs the
    # article's own baseline = reframe-by-churn in a medium article (the Palestinian-political-violence
    # gap). L1's ratio gate reads HEALTHY and the absolute LEAD_FLOOR misses it, so route to L2 for a human.
    # Only when removal→PWR did NOT already fire (a big removal is the retrofit path, handled above).
    if "removal→PWR" not in leads and pk["removed"] >= CHURN_MIN_BYTES and rem_anom >= CHURN_ANOMALY:
        leads.append("churn→L2")

    return {
        "article": article,
        "removed": pk["removed"], "rem_anom": rem_anom,
        "rem_window": (pk["start"][:10], pk["end"][:10]),
        "added": pa["added"], "add_anom": add_anom,
        "add_window": (pa["start"][:10], pa["end"][:10]),
        "editor_conc": top_n / pk["revs"] if pk["revs"] else 0,
        "leads": leads,
        "total_revs": len(rows),
    }


# known PWR/base-rate verdicts, to score the pre-filter's recall (not used for ranking)
KNOWN_PIVOT = {"Zionism", "Anti-Zionism", "Israeli–Palestinian conflict", "Climate change", "Abortion"}
KNOWN_HEALTHY = {"Water", "Photosynthesis", "Brontosaurus", "Nakba", "Chess"}


def run(targets=None):
    con = duckdb.connect(str(config.DB), read_only=True)
    if not targets:
        targets = Corpus(con).distinct_articles()
    results = [r for r in (prerank(con, a) for a in targets) if r]
    con.close()
    results.sort(key=lambda r: -r["removed"])

    print(f"\n{'article':<30} {'rem_peak_B':>10} {'add_peak_B':>10} {'ed%':>4}  {'leads':<20} rem/add windows")
    print("-" * 112)
    for r in results:
        tag = "PIVOT" if r["article"] in KNOWN_PIVOT else ("healthy" if r["article"] in KNOWN_HEALTHY else "?")
        print(f"{r['article']:<30} {r['removed']:>10,} {r['added']:>10,} {r['editor_conc']*100:>3.0f}%  "
              f"{(', '.join(r['leads']) or '—'):<20} R:{r['rem_window'][0]} A:{r['add_window'][0]} [{tag}]")

    pivots = [r for r in results if r["article"] in KNOWN_PIVOT]
    healthy = [r for r in results if r["article"] in KNOWN_HEALTHY]
    if pivots and healthy:
        min_pivot = min(r["removed"] for r in pivots)
        below = [r["article"] for r in healthy if r["removed"] >= min_pivot]
        print("-" * 112)
        print(f"lowest known-PIVOT removed_bytes: {min_pivot:,}  "
              f"({[r['article'] for r in pivots if r['removed']==min_pivot][0]})")
        print(f"known-HEALTHY at/above that floor (acceptable false positives for a recall filter): {below or 'none'}")
    addition_leads = [r["article"] for r in results if "addition→L2" in r["leads"]]
    print(f"addition→L2 leads (reframe-by-addition candidates the removal metric would miss): {addition_leads or 'none'}")

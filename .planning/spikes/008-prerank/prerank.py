"""Spike 008 (★#2) — metadata-only candidate pre-ranker. NO article text, NO WikiWho.

Thesis (prior-art ★#2 / #8): before spending expensive token-level PWR analysis (spike 005),
cheaply pre-rank articles from revision METADATA alone — the columns Quarry / Wiki Replicas expose
in bulk via SQL (rev timestamp, byte size, actor). We already cache the equivalent in DuckDB
(`revisions`.ts/user + `rev_size`.size, fetched via the Action API), so this validates the thesis
offline. Production would source the same columns in bulk from Quarry / Wiki Replicas / dumps rather
than per-article API calls.

Signal: per time-bin byte deltas split into removed vs added bytes. `removed_bytes` (Σ of negative
deltas) is a cheap metadata analog of PWR-mass "spine destroyed" — it targets the removal thesis and
separates a retrofit (big removals) from pure expansion (mostly additions). Score = peak-bin removed
bytes, plus an anomaly ratio vs the article's own baseline.

ROBUSTNESS (learned from spike 005): raw byte deltas are dominated by transient vandalism (blank −30k,
restore +30k → nets to zero but inflates churn). So deltas are computed on a ROLLING-MEDIAN-SMOOTHED
size series, which rejects blank/restore spikes and keeps only sustained changes — the metadata analog
of 005's "persistent revision ≈ local median size."

This is a RECALL-oriented pre-filter, not a verdict: it must rank true candidates high enough that we
never skip them; false positives are fine — the PWR engine (005) confirms precision. Necessary, not
sufficient — a candidate is a lead.

Usage: uv run python prerank.py                 # rank whole cached set
       uv run python prerank.py "Zionism" ...    # specific articles
"""
import sys
import pathlib
import statistics
import datetime as dt
import duckdb

DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"
BIN_DAYS = 180          # calendar bin width; production can sweep, WikiBlame-style, once flagged
MIN_REVS = 50           # too little history to pre-rank meaningfully
SMOOTH_K = 5            # rolling-median half-window (revisions) — rejects transient blank/restore
LEAD_FLOOR = 50_000     # provisional (calibrate in ★#3): min peak-bin bytes to raise a lead
ANOMALY_MIN = 5.0       # provisional: min × the article's own baseline to raise a lead
GROWTH_RATIO = 3.0      # addition lead: peak-added bin must be this-× net-growth (added ≫ removed)


def _bin_index(ts_iso, t0):
    d = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return (d - t0).days // BIN_DAYS


def _rolling_median(sizes, half=SMOOTH_K):
    """Median-filter the size series so isolated vandalism spikes (blank/restore) don't register as
    content churn; sustained changes survive. Metadata analog of 005's persistent-revision snapshot."""
    n = len(sizes)
    return [statistics.median(sizes[max(0, i - half):min(n, i + half + 1)]) for i in range(n)]


def prerank(con, article):
    rows = con.execute("""
        SELECT r.ts, z.size, r.user
        FROM revisions r JOIN rev_size z ON z.article=r.article AND z.rev_id=r.rev_id
        WHERE r.article=? ORDER BY r.ts
    """, [article]).fetchall()
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
        leads.append("removal→PWR")                              # retrofit candidate → token engine (005)
    # Addition lead: a large, net-growth addition burst (added ≫ removed) — the reframe-by-addition
    # vector the removal metric is BLIND to (e.g. Nakba's post-Oct-7 expansion). Route to L2 (006),
    # never dismiss as "growth". Growth alone is normal; a large sourced expansion can still reframe.
    if pa["added"] >= LEAD_FLOOR and add_anom >= ANOMALY_MIN and pa["added"] > GROWTH_RATIO * pa["removed"]:
        leads.append("addition→L2")

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


if __name__ == "__main__":
    con = duckdb.connect(str(DB), read_only=True)
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = [r[0] for r in con.execute("SELECT DISTINCT article FROM revisions ORDER BY article").fetchall()]
    results = [r for r in (prerank(con, a) for a in targets) if r]
    con.close()
    results.sort(key=lambda r: -r["removed"])

    print(f"\n{'article':<30} {'rem_peak_B':>10} {'add_peak_B':>10} {'ed%':>4}  {'leads':<20} rem/add windows")
    print("-" * 112)
    for r in results:
        tag = "PIVOT" if r["article"] in KNOWN_PIVOT else ("healthy" if r["article"] in KNOWN_HEALTHY else "?")
        print(f"{r['article']:<30} {r['removed']:>10,} {r['added']:>10,} {r['editor_conc']*100:>3.0f}%  "
              f"{(', '.join(r['leads']) or '—'):<20} R:{r['rem_window'][0]} A:{r['add_window'][0]} [{tag}]")

    # recall check: would a cheap removal-cut keep every known PIVOT?
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

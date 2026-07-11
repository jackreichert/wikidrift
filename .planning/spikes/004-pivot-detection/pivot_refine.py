"""Spike 004 stage 2 — binary-search the exact pivot revision inside the coarse-flagged interval.

Stage 1 (pivot_detect.py) localizes the pivot to a ~6-month interval and classifies pivot vs creep.
Stage 2 (here) fixes the established-token cohort C at the interval start and binary-searches the
revision timeline for the dominant DROP in f(rev) = |C ∩ tokens(rev)| / |C| — recursing into the
half with the larger decline (robust to re-insertion noise + finds multiple steps).

Pinpointing the revision yields the editor(s) + diff responsible → the input Layer 2 needs.

Usage: uv run python pivot_refine.py "Zionism"   (auto-picks the peak interval from stage-1 snapshots)
"""
import sys
import pathlib
import requests
import duckdb

UA = "gh-wiki-spike/0.1 (awesome@rpophesagr.com)"
WIKIWHO = "https://wikiwho.wmcloud.org/en/api/v1.0.0-beta"
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"
_cache = {}


def tokens_at(article, rev_id):
    if rev_id in _cache:
        return _cache[rev_id]
    url = f"{WIKIWHO}/rev_content/{article}/{rev_id}/?token_id=true&out=false&in=false"
    d = requests.get(url, headers={"User-Agent": UA}, timeout=180).json()
    rev = d["revisions"][0]
    rid = next(iter(rev))
    s = {t["token_id"] for t in rev[rid]["tokens"]}
    _cache[rev_id] = s
    return s


def peak_interval(con, article):
    """Recompute established-text deletion per consecutive snapshot pair; return the peak interval."""
    snaps = con.execute(
        "SELECT DISTINCT snap_date, snap_rev FROM snapshots WHERE article=? ORDER BY snap_date", [article]).fetchall()
    best = None
    for (d0, r0), (d1, r1) in zip(snaps, snaps[1:]):
        estdel = con.execute("""
            WITH a AS (SELECT token_id, CAST(rr.ts AS TIMESTAMP) origin FROM snapshots s
                       JOIN revisions rr ON rr.article=s.article AND rr.rev_id=s.o_rev_id
                       WHERE s.article=? AND s.snap_rev=?),
                 b AS (SELECT token_id FROM snapshots WHERE article=? AND snap_rev=?)
            SELECT 100.0*(SELECT count(*) FROM a WHERE date_diff('day',origin,CAST(? AS TIMESTAMP))>=730
                          AND token_id NOT IN (SELECT token_id FROM b))
                   /NULLIF((SELECT count(*) FROM a WHERE date_diff('day',origin,CAST(? AS TIMESTAMP))>=730),0)
        """, [article, r0, article, r1, d0+" 00:00:00", d0+" 00:00:00"]).fetchone()[0] or 0
        if best is None or estdel > best[0]:
            best = (estdel, d0, r0, d1, r1)
    return best


def revs_between(con, article, d0, d1):
    return con.execute(
        "SELECT rev_id, ts, user FROM revisions WHERE article=? AND ts>? AND ts<=? ORDER BY ts",
        [article, d0+"T00:00:00Z", d1+"T00:00:00Z"]).fetchall()


def main(article):
    con = duckdb.connect(str(DB), read_only=True)
    pk = peak_interval(con, article)
    estdel, d0, r0, d1, r1 = pk
    print(f"{article}: peak coarse interval {d0} -> {d1}  (established-text deletion {estdel:.1f}%)")

    # cohort C = established (>=2yr old) tokens present at interval start r0
    cohort = set(con.execute("""
        SELECT s.token_id FROM snapshots s
        JOIN revisions rr ON rr.article=s.article AND rr.rev_id=s.o_rev_id
        WHERE s.article=? AND s.snap_rev=? AND date_diff('day',CAST(rr.ts AS TIMESTAMP),CAST(? AS TIMESTAMP))>=730
    """, [article, r0, d0+" 00:00:00"]).fetchall())
    cohort = {t[0] for t in cohort} if isinstance(next(iter(cohort), (0,)), tuple) else cohort
    revs = revs_between(con, article, d0, d1)
    con.close()
    if not cohort or len(revs) < 2:
        print("  (insufficient cohort/revisions to refine)"); return
    print(f"  established cohort |C| = {len(cohort):,} tokens; {len(revs):,} revisions in interval")

    def f(idx):
        return len(cohort & tokens_at(article, revs[idx][0])) / len(cohort)

    # recursive descend into the half with the larger decline in surviving fraction
    steps = []
    def refine(lo, hi, flo, fhi, depth=0):
        if hi - lo <= 1:
            return [(lo, hi, flo - fhi)]
        mid = (lo + hi) // 2
        fmid = f(mid)
        steps.append((revs[mid][1][:10], fmid))
        left, right = flo - fmid, fmid - fhi
        # descend into the bigger drop; if both large (multi-step), take both
        out = []
        if left >= right:
            out += refine(lo, mid, flo, fmid, depth+1)
            if right > 0.15:  # secondary step worth reporting
                out += refine(mid, hi, fmid, fhi, depth+1)
        else:
            out += refine(mid, hi, fmid, fhi, depth+1)
            if left > 0.15:
                out += refine(lo, mid, flo, fmid, depth+1)
        return out

    flo, fhi = f(0), f(len(revs)-1)
    drops = refine(0, len(revs)-1, flo, fhi)
    print(f"  surviving fraction of C: {flo*100:.1f}% at interval start -> {fhi*100:.1f}% at end")
    print(f"  bisection path (date, surviving%): " + " ".join(f"{d}:{v*100:.0f}%" for d, v in steps))
    print("\n  PINPOINTED DROP(S):")
    for lo, hi, drop in sorted(drops, key=lambda x: -x[2]):
        r_before, r_after = revs[lo], revs[hi]
        print(f"    between rev {r_before[0]} ({r_before[1][:10]}) and rev {r_after[0]} ({r_after[1][:10]})")
        print(f"      established-spine drop across this step: {drop*100:.1f} pts")
        # editors acting in this narrow window
        eds = {}
        for rid, ts, user in revs[lo:hi+1]:
            eds[user] = eds.get(user, 0) + 1
        top = sorted(eds.items(), key=lambda x: -x[1])[:5]
        print(f"      editors in window: " + ", ".join(f"{u}({n})" for u, n in top))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Zionism")

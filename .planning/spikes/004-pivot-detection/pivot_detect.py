"""Spike 004 (Layer 1.5) — find the PIVOT POINT in an article's history, editor-agnostically.

We do NOT assume a date. We snapshot the article's token set at regular intervals (via the
WikiWho historical-revision endpoint), then for each interval measure how much PRE-EXISTING,
already-established text was destroyed. Output a time series + the detected inflection.

  pivot  = one interval where destruction of established text spikes (a deliberate rewrite)
  creep  = elevated destruction spread across many intervals (gradual drift)
  healthy = established text persists; only recent text churns

Signals per interval (t_i -> t_{i+1}), matched by stable WikiWho token_id:
  size            = token count at t_i
  retention       = |tokens(t_i) ∩ tokens(t_{i+1})| / |tokens(t_i)|
  established_del  = of tokens already >=2yr old at t_i, fraction deleted by t_{i+1}
                     (isolates destruction of the stable spine from normal recent churn)

Usage: uv run python pivot_detect.py "Zionism"
"""
import sys
import pathlib
import datetime as dt
import requests
import duckdb

UA = "gh-wiki-spike/0.1 (awesome@rpophesagr.com)"
WIKIWHO = "https://wikiwho.wmcloud.org/en/api/v1.0.0-beta"
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"


def semiannual(start_year, end_year):
    dates = []
    for y in range(start_year, end_year + 1):
        for m in (1, 7):
            dates.append(f"{y}-{m:02d}-01")
    return dates


def rev_at(con, article, date):
    r = con.execute(
        "SELECT rev_id, ts FROM revisions WHERE article=? AND ts <= ? ORDER BY ts DESC LIMIT 1",
        [article, date + "T00:00:00Z"]).fetchone()
    return r  # (rev_id, ts) or None


def fetch_snapshot(article, rev_id):
    url = f"{WIKIWHO}/rev_content/{article}/{rev_id}/?token_id=true&o_rev_id=true&out=false&in=false"
    d = requests.get(url, headers={"User-Agent": UA}, timeout=180).json()
    if not d.get("success"):
        return None
    rev = d["revisions"][0]
    rid = next(iter(rev))
    return rev[rid]["tokens"]


def ensure_snapshot(con, article, date, rev_id):
    have = con.execute("SELECT count(*) FROM snapshots WHERE article=? AND snap_rev=?",
                       [article, rev_id]).fetchone()[0]
    if have:
        return
    toks = fetch_snapshot(article, rev_id)
    if not toks:
        return
    con.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                    [(article, date, rev_id, t["token_id"], t["o_rev_id"]) for t in toks])


def main(article):
    con = duckdb.connect(str(DB))
    con.execute("CREATE TABLE IF NOT EXISTS snapshots(article TEXT, snap_date TEXT, snap_rev BIGINT, token_id BIGINT, o_rev_id BIGINT)")

    first_ts = con.execute("SELECT min(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    start_year = max(2008, int(first_ts[:4]))
    end_year = int(con.execute("SELECT max(ts) FROM revisions WHERE article=?", [article]).fetchone()[0][:4])

    snaps = []  # (date, rev_id)
    for d in semiannual(start_year, end_year):
        r = rev_at(con, article, d)
        if r and (not snaps or r[0] != snaps[-1][1]):
            ensure_snapshot(con, article, d, r[0])
            snaps.append((d, r[0]))
    print(f"{article}: {len(snaps)} snapshots {snaps[0][0]}..{snaps[-1][0]}", flush=True)

    # per-interval metrics
    print(f"\n{'interval end':>12} | {'size':>7} | {'retention':>9} | {'established_del':>15}")
    print("-" * 54)
    series = []
    for i in range(len(snaps) - 1):
        (d0, r0), (d1, r1) = snaps[i], snaps[i + 1]
        row = con.execute("""
            WITH a AS (SELECT token_id, CAST(rr.ts AS TIMESTAMP) origin FROM snapshots s
                       JOIN revisions rr ON rr.article=s.article AND rr.rev_id=s.o_rev_id
                       WHERE s.article=? AND s.snap_rev=?),
                 b AS (SELECT token_id FROM snapshots WHERE article=? AND snap_rev=?)
            SELECT
              (SELECT count(*) FROM a) AS size,
              100.0*(SELECT count(*) FROM a WHERE token_id IN (SELECT token_id FROM b))/NULLIF((SELECT count(*) FROM a),0) AS retention,
              100.0*(SELECT count(*) FROM a WHERE date_diff('day', origin, CAST(? AS TIMESTAMP))>=730 AND token_id NOT IN (SELECT token_id FROM b))
                    /NULLIF((SELECT count(*) FROM a WHERE date_diff('day', origin, CAST(? AS TIMESTAMP))>=730),0) AS established_del
        """, [article, r0, article, r1, d0+" 00:00:00", d0+" 00:00:00"]).fetchone()
        size, ret, estdel = row
        series.append((d1, estdel or 0.0))
        bar = "#" * int((estdel or 0) / 3)
        print(f"{d1:>12} | {size:>7,} | {ret or 0:>8.1f}% | {estdel or 0:>13.1f}% {bar}")

    # simple change-point: which interval(s) exceed mean+2*std of established_del
    vals = [v for _, v in series]
    n = len(vals); mean = sum(vals)/n; std = (sum((v-mean)**2 for v in vals)/n) ** 0.5
    thresh = mean + 2*std
    spikes = [(d, v) for d, v in series if v >= thresh]
    peak = max(series, key=lambda x: x[1])
    frac_in_peak = peak[1] / sum(vals) if sum(vals) else 0
    print("-" * 54)
    print(f"\nestablished-text deletion: mean {mean:.1f}%/interval, std {std:.1f}, peak {peak[1]:.1f}% @ {peak[0]}")
    verdict = "PIVOT" if peak[1] >= thresh and peak[1] > 3*mean else ("CREEP" if mean > 10 else "HEALTHY/stable")
    print(f"detected: {verdict}")
    if spikes:
        print("spike intervals (> mean+2σ): " + ", ".join(f"{d} ({v:.0f}%)" for d, v in spikes))
    con.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Zionism")

"""Spike 005 (Layer 1) — robust, article-agnostic drift analyzer with attribution.

Pipeline:
  1. sizes    — fetch per-revision byte size (Action API) so we can reject transient
                blanking/vandalism when snapshotting.
  2. snapshot — at each semiannual date, pick a PERSISTENT revision (size ≈ local median),
                not just the last-before-date (which can be a vandalized state).
  3. coarse   — persistence-weighted content loss per interval (PWR-grounded: each token is
                weighted by earned survival), with a maturity skip; classify HEALTHY/CREEP/PIVOT.
  4. refine   — binary-search the peak interval for the exact drop revision window,
                confirming the durable (high-persistence) spine actually collapsed.
  5. attribute— WHO did it: destroyers (editors whose revisions removed established-spine
                tokens, via each token's terminal `out`) + replacers (origin editors of the
                current post-pivot text).

Metric grounding (the drift signal is NOT ad-hoc — it is content-survival):
  Each token carries a persistence weight w(t) = the number of snapshots it has survived
  since origin — a snapshot-sampled analog of Halfaker et al.'s Persistent-Word-Revisions
  ("A jury of your peers", WikiSym 2009) and Adler & de Alfaro's content-survival / text-life
  ("A content-driven reputation system for Wikipedia", WWW 2007). A token's value is the peer
  review it has survived, not its raw age. The drift signal is persistence-weighted content
  loss; the old raw established-deletion % is the degenerate case w≡1 behind a hard 730-day
  cohort cliff. NB: this is a *change* detector, not a *bias* detector (base-rate finding);
  PWR makes the magnitude citable — it does not, alone, distinguish capture from legit rewrite.

Verdict ranking: confirmed episodes are ranked by PWR-mass (age-agnostic — an old capture that persisted is
  still a find; long-standing distortions like KL Warschau are a primary target, so age must NOT bury them).
  Recency is reported as a DESCRIPTOR only — "recent retrofit" vs "standing distortion (persisted Nyr)" —
  never a demoter. A tiny old blip (Water 2007) is demoted by its small MASS, not its age.

Usage: uv run python analyze.py "Zionism"
"""
import sys
import time
import pathlib
import statistics
import datetime as dt
import urllib.parse
from bisect import bisect_right
import requests
import duckdb

UA = "gh-wiki-spike/0.1 (awesome@rpophesagr.com)"
WIKIWHO = "https://wikiwho.wmcloud.org/en/api/v1.0.0-beta"
ACTION = "https://en.wikipedia.org/w/api.php"
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"
S = requests.Session(); S.headers.update({"User-Agent": UA})
_tok = {}

MIN_COHORT = 500        # refine: min durable-spine tokens needed to binary-search a drop
MIN_MATURE = 15000      # only analyze once the article has >= this many tokens (skip stub-era churn)
MAG_FLOOR = 25.0        # min persistence-weighted loss % in an interval to consider a PIVOT
CONFIRM_DROP = 0.20     # binary search must confirm the durable spine declined by >= this fraction
CREEP_MEAN = 8.0        # sustained mean weighted-loss above this (no single pivot) = CREEP
DURABLE_Q = 0.50        # refine cohort = tokens present at interval start above this persistence quantile
RECENT_YEARS = 3.0      # episodes ending within this of the horizon are tagged "recent" (else "standing")


def ensure_sizes(con, article):
    """Populate BOTH the revision timeline (rev_id→ts/user) and per-rev size in one Action-API sweep."""
    con.execute("CREATE TABLE IF NOT EXISTS revisions(article TEXT, rev_id BIGINT, ts TEXT, user TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS rev_size(article TEXT, rev_id BIGINT, size BIGINT)")
    have_rev = con.execute("SELECT count(*) FROM revisions WHERE article=?", [article]).fetchone()[0]
    have_sz = con.execute("SELECT count(*) FROM rev_size WHERE article=?", [article]).fetchone()[0]
    if have_rev and have_sz:
        return
    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
              "titles": article, "rvprop": "ids|timestamp|user|size", "rvlimit": "max", "rvdir": "newer", "maxlag": "5"}
    revrows, szrows = [], []
    while True:
        for attempt in range(4):                   # retry: network/Action-API can be flaky
            try:
                d = S.get(ACTION, params=params, timeout=25).json()
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        for pg in d.get("query", {}).get("pages", []):
            for rv in pg.get("revisions", []):
                rid = int(rv["revid"])
                revrows.append((article, rid, rv["timestamp"], rv.get("user", "<hidden>")))
                szrows.append((article, rid, int(rv.get("size", 0))))
        if "continue" in d:
            params["rvcontinue"] = d["continue"]["rvcontinue"]
        else:
            break
    if not have_rev:
        con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", revrows)
    if not have_sz:
        con.executemany("INSERT INTO rev_size VALUES (?,?,?)", szrows)
    print(f"  history: {len(revrows):,} revisions", flush=True)


def tokens_at(article, rev_id, io=False):
    key = (rev_id, io)
    if key in _tok:
        return _tok[key]
    extra = "&out=true&in=true" if io else "&out=false&in=false"
    url = f"{WIKIWHO}/rev_content/{urllib.parse.quote(article, safe='')}/{rev_id}/?token_id=true&o_rev_id=true{extra}"
    for attempt in range(4):                       # retry: WikiWho throws transient errors under load
        try:
            d = S.get(url, timeout=25).json()      # fail fast; WMCloud is flaky, don't hang 180s per call
            if d.get("revisions"):
                rev = d["revisions"][0]; rid = next(iter(rev))
                _tok[key] = rev[rid]["tokens"]
                return _tok[key]
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    _tok[key] = []                                 # unavailable after retries → caller skips
    return []


def persistent_rev(con, article, date, window_days=21):
    """Pick a revision near `date` whose size ≈ the local median (rejects transient blanking)."""
    rows = con.execute("""
        SELECT r.rev_id, r.ts, z.size,
               abs(date_diff('day', CAST(r.ts AS TIMESTAMP), CAST(? AS TIMESTAMP))) AS dd
        FROM revisions r JOIN rev_size z ON z.article=r.article AND z.rev_id=r.rev_id
        WHERE r.article=? AND abs(date_diff('day', CAST(r.ts AS TIMESTAMP), CAST(? AS TIMESTAMP))) <= ?
        ORDER BY dd
    """, [date+" 00:00:00", article, date+" 00:00:00", window_days]).fetchall()
    if not rows:
        r = con.execute("""SELECT r.rev_id, r.ts FROM revisions r
            WHERE r.article=? AND r.ts<=? ORDER BY r.ts DESC LIMIT 1""", [article, date+"T00:00:00Z"]).fetchone()
        return r
    med = statistics.median([s for _, _, s, _ in rows])
    # nearest-to-date revision whose size is within 25% of the local median
    for rev_id, ts, size, _ in rows:
        if med and abs(size - med) <= 0.25 * med:
            return (rev_id, ts)
    return (rows[0][0], rows[0][1])


def build_snapshots(con, article):
    con.execute("CREATE TABLE IF NOT EXISTS rsnap(article TEXT, snap_date TEXT, snap_rev BIGINT, token_id BIGINT, o_rev_id BIGINT)")
    first = con.execute("SELECT min(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    last = con.execute("SELECT max(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    total_revs = con.execute("SELECT count(*) FROM revisions WHERE article=?", [article]).fetchone()[0]
    # adaptive cadence: annual for large articles (keeps rsnap small + fewer intervals); binary search refines timing anyway
    months = (1, 7) if total_revs <= 8000 else (1,)
    dates = [f"{y}-{m:02d}-01" for y in range(int(first[:4]), int(last[:4]) + 1) for m in months]
    snaps = []
    for d in dates:
        pr = persistent_rev(con, article, d)
        if not pr or (snaps and pr[0] == snaps[-1][1]):
            continue
        rev_id = pr[0]
        if not con.execute("SELECT 1 FROM rsnap WHERE article=? AND snap_rev=?", [article, rev_id]).fetchone():
            toks = tokens_at(article, rev_id)
            if not toks:
                continue                            # WikiWho couldn't serve this revision — skip snapshot
            con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)",
                            [(article, d, rev_id, t["token_id"], t["o_rev_id"]) for t in toks])
            time.sleep(0.3)                         # be polite to the WikiWho API
        snaps.append((d, rev_id))
    return snaps


def load_membership(con, article):
    """Snapshot membership + PWR weights, computed once from rsnap (no WikiWho calls).

    Returns (snaps, members, present, idx_of_rev):
      snaps    — [(snap_date, snap_rev)] ordered in time
      members  — [set(token_id)] per snapshot index
      present  — {token_id: [snapshot indices it appears in]} (ascending → sorted)
      idx_of_rev — {snap_rev: snapshot index}
    Weight w(t,k) = # snapshots ≤ k containing t (see `_pwr`)."""
    snaps = con.execute(
        "SELECT DISTINCT snap_date, snap_rev FROM rsnap WHERE article=? ORDER BY snap_date, snap_rev",
        [article]).fetchall()
    members = [set() for _ in snaps]
    present = {}
    for i, (sd, sr) in enumerate(snaps):
        for (t,) in con.execute("SELECT token_id FROM rsnap WHERE article=? AND snap_rev=?", [article, sr]).fetchall():
            members[i].add(t)
            present.setdefault(t, []).append(i)     # appended in index order → already sorted
    idx_of_rev = {sr: i for i, (sd, sr) in enumerate(snaps)}
    return snaps, members, present, idx_of_rev


def _pwr(present, token, k):
    """Earned survival (persistent-word-snapshots) of `token` as of snapshot k."""
    return bisect_right(present[token], k)


def coarse(snaps, members, present, quiet=False):
    """Per-interval persistence-weighted content loss — the PWR-grounded drift metric.

    ratio D = Σ w(t) over tokens lost in [k,k+1] / Σ w(t) over tokens present at k;
    absolute magnitude = Σ w(t) destroyed (the episode-ranking key). `quiet` suppresses the
    per-interval table (for batch callers like the benchmark)."""
    if not quiet:
        print(f"\n{'interval end':>12} | {'size':>7} | {'pwr_loss':>8} | {'pwr_destroyed':>13}")
        print("-"*52)
    series = []
    for k in range(len(snaps) - 1):
        d0, r0 = snaps[k]; d1, r1 = snaps[k + 1]
        at0, at1 = members[k], members[k + 1]
        if not at0:
            continue
        lost = at0 - at1
        w0 = sum(_pwr(present, t, k) for t in at0)
        wlost = sum(_pwr(present, t, k) for t in lost)
        ratio = 100.0 * wlost / w0 if w0 else 0.0
        size = len(at0)
        mature = size >= MIN_MATURE
        if not quiet:
            flag = "" if mature else "  (immature — excluded)"
            bar = "#"*int(ratio/3) if mature else ""
            print(f"{d1:>12} | {size:>7,} | {ratio:>7.1f}% | {wlost:>13,} {bar}{flag}")
        if mature:
            series.append((d0, r0, d1, r1, ratio, size, wlost))
    vals = [row[4] for row in series]
    if not vals:
        return [], (0, 0, 0)
    mean = statistics.mean(vals); med = statistics.median(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0
    if not quiet:
        print("-"*52)
        print(f"persistence-weighted loss: mean {mean:.1f}%  median {med:.1f}%  peak {max(vals):.1f}%")
    return series, (mean, med, std)


def build_episodes(series, elevated=15.0):
    """Group time-contiguous intervals with persistence-weighted loss >= `elevated` into episodes.
    `abs` accumulates PWR-mass destroyed (the ranking key); `peak` is the max interval loss %."""
    episodes, cur = [], None
    for d0, r0, d1, r1, ratio, size, absd in series:
        if ratio >= elevated:
            if cur and cur["end"][0] == d0:                 # time-contiguous with the running episode
                cur["end"] = (d1, r1); cur["abs"] += absd; cur["peak"] = max(cur["peak"], ratio)
            else:
                if cur: episodes.append(cur)
                cur = {"start": (d0, r0), "end": (d1, r1), "abs": absd, "peak": ratio}
        elif cur:
            episodes.append(cur); cur = None
    if cur: episodes.append(cur)
    return episodes


def refine(article, con, snaps, members, present, idx_of_rev, peak):
    d0, r0, d1, r1, _ = peak
    k = idx_of_rev.get(r0)
    at0 = members[k] if k is not None else set()
    if not at0:
        print("  (no snapshot membership to refine)"); return None
    # durable spine = tokens present at interval start above the persistence quantile
    # (the PWR-grounded replacement for the old hard 730-day "established" cliff)
    weights = sorted(_pwr(present, t, k) for t in at0)
    cut = weights[int(DURABLE_Q * (len(weights) - 1))]
    cohort = {t for t in at0 if _pwr(present, t, k) >= cut}
    revs = con.execute("SELECT rev_id, ts, user FROM revisions WHERE article=? AND ts>? AND ts<=? ORDER BY ts",
                       [article, d0+"T00:00:00Z", d1+"T00:00:00Z"]).fetchall()
    if len(cohort) < MIN_COHORT or len(revs) < 3:
        print("  (interval too small to refine)"); return None
    f = lambda i: len({t["token_id"] for t in tokens_at(article, revs[i][0])} & cohort) / len(cohort)
    f_start, f_end = f(0), f(len(revs)-1)
    interval_drop = f_start - f_end   # durable-spine survival decline across the whole interval
    lo, hi, flo, fhi = 0, len(revs)-1, f_start, f_end
    path = []
    while hi - lo > 1:
        mid = (lo+hi)//2; fmid = f(mid); path.append((revs[mid][1][:10], fmid))
        if flo - fmid >= fmid - fhi:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    print(f"\n  binary search on durable spine |C|={len(cohort):,} (w≥{cut}): "
          f"{f_start*100:.0f}% → {f_end*100:.0f}%  (interval decline {interval_drop*100:.0f} pts)")
    print(f"  path: " + " ".join(f"{d}:{v*100:.0f}%" for d, v in path))
    print(f"  ⇒ dominant drop between rev {revs[lo][0]} ({revs[lo][1][:10]}) and rev {revs[hi][0]} ({revs[hi][1][:10]})")
    return revs[lo], revs[hi], interval_drop


def attribute(article, con, peak):
    """WHO did it — destroyers (removed established spine) and replacers (wrote the new text)."""
    d0, r0, d1, r1, _ = peak
    print(f"\n  ── WHO DID IT ({d0} → {d1}) ──")
    # cohort tokens (established at interval start) with their full in/out history
    snap = tokens_at(article, r0, io=True)
    # "current tokens" = the latest snapshot we actually have for this article (rsnap), NOT the stale
    # `tokens` table (which only held the original 001a articles → empty here → over-counted kills).
    latest = con.execute("SELECT snap_rev FROM rsnap WHERE article=? ORDER BY snap_date DESC LIMIT 1", [article]).fetchone()
    cur = set()
    if latest:
        cur = {r[0] for r in con.execute("SELECT token_id FROM rsnap WHERE article=? AND snap_rev=?", [article, latest[0]]).fetchall()}
    origin_ts = dict(con.execute("SELECT rev_id, ts FROM revisions WHERE article=?", [article]).fetchall())
    editor_of = dict(con.execute("SELECT rev_id, user FROM revisions WHERE article=?", [article]).fetchall())
    d0ts = d0 + "T00:00:00Z"; d1ts = d1 + "T00:00:00Z"
    # a cohort token = established (>=2yr before d0) AND present at r0
    killers = {}
    killed = 0
    for t in snap:
        o = t["o_rev_id"]; ots = origin_ts.get(o)
        if not ots or ots >= d0ts:  # not established before the interval
            continue
        # established. Did it die in this interval and stay dead?
        outs = [x for x in t.get("out", []) if editor_of.get(x)]
        if not outs:
            continue
        death = max(outs)
        dts = origin_ts.get(death)
        if dts and d0ts < dts <= d1ts and t["token_id"] not in cur:
            killers[editor_of.get(death, "?")] = killers.get(editor_of.get(death, "?"), 0) + 1
            killed += 1
    print(f"  DESTROYERS — editors who removed established-spine tokens in this window ({killed:,} tokens killed):")
    for u, n in sorted(killers.items(), key=lambda x: -x[1])[:8]:
        print(f"    {n:>6,}  {u}")
    # replacers: origin editors (usernames) of CURRENT (latest-snapshot) tokens introduced after d0
    reps = {}
    if latest:
        for tok_id, o_rev in con.execute("SELECT token_id, o_rev_id FROM rsnap WHERE article=? AND snap_rev=?", [article, latest[0]]).fetchall():
            ots = origin_ts.get(o_rev)
            if ots and ots > d0ts:
                u = editor_of.get(o_rev, "?"); reps[u] = reps.get(u, 0) + 1
    top = sorted(reps.items(), key=lambda x: -x[1])[:8]
    total_new = sum(n for _, n in top)
    print(f"  REPLACERS — top authors of current text written after {d0} ({total_new:,}+ tokens shown):")
    for u, n in top:
        print(f"    {n:>6,}  {u}")


def ensure_indexes(con):
    """Indexes are load-bearing: rsnap grows to millions of rows; without these the per-date
    existence checks and interval joins become full-table scans (the perf pathology)."""
    con.execute("CREATE TABLE IF NOT EXISTS rsnap(article TEXT, snap_date TEXT, snap_rev BIGINT, token_id BIGINT, o_rev_id BIGINT)")
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_rsnap_art_rev ON rsnap(article, snap_rev)",
        "CREATE INDEX IF NOT EXISTS ix_rsnap_art_tok ON rsnap(article, token_id)",
        "CREATE INDEX IF NOT EXISTS ix_rev_art_id ON revisions(article, rev_id)",
        "CREATE INDEX IF NOT EXISTS ix_revsize_art_id ON rev_size(article, rev_id)",
        "CREATE INDEX IF NOT EXISTS ix_tokens_art_tok ON tokens(article, token_id)",
    ):
        try:
            con.execute(stmt)
        except Exception:
            pass


def _age_years(end_date, horizon):
    """Years from an episode's end to the analysis horizon (the article's last snapshot) — deterministic,
    no wall-clock. The horizon, not `now`, keeps results reproducible across re-runs."""
    return max(0.0, (dt.date.fromisoformat(horizon) - dt.date.fromisoformat(end_date)).days / 365.25)


def annotate_episodes(episodes, horizon):
    """Annotate each episode with age and RANK BY PWR-mass (age-agnostic). Recency is DESCRIPTIVE, never a
    demoter: a large *old* drift that persisted is a standing distortion (a long-standing-distortion
    candidate, cf. KL Warschau) — an old capture is still a find, so age must not bury it. A tiny old blip
    (Water 2007) is demoted by its small MASS, not its age. (Operator correction, 2026-07-07.)"""
    for e in episodes:
        e["age"] = _age_years(e["end"][0], horizon)
    episodes.sort(key=lambda e: -e["abs"])
    return episodes


def _recency_tag(age):
    return "recent" if age <= RECENT_YEARS else f"standing {age:.0f}yr"


def main(article):
    con = duckdb.connect(str(DB))
    print(f"=== ANALYZE: {article} ===", flush=True)
    ensure_sizes(con, article)
    ensure_indexes(con)
    build_snapshots(con, article)
    snaps, members, present, idx_of_rev = load_membership(con, article)
    if len(snaps) < 3:
        print("  too few snapshots to analyze"); con.close(); return
    print(f"  {len(snaps)} persistent snapshots {snaps[0][0]}..{snaps[-1][0]}", flush=True)
    series, (mean, med, std) = coarse(snaps, members, present)
    horizon = snaps[-1][0]
    episodes = annotate_episodes([e for e in build_episodes(series) if e["peak"] >= MAG_FLOOR], horizon)

    if episodes:
        print(f"\ncandidate pivot episodes (ranked by PWR-mass destroyed; recency = context, NOT a demoter):")
        for e in episodes:
            print(f"  {e['start'][0]} → {e['end'][0]}   peak {e['peak']:.0f}%   ~{int(e['abs']):,} PWR   "
                  f"age {e['age']:.1f}yr  [{_recency_tag(e['age'])}]")
        confirmed = []
        for e in episodes[:3]:                      # confirm the top few (by PWR-mass) via binary search
            span = (e["start"][0], e["start"][1], e["end"][0], e["end"][1], e["peak"])
            print(f"\n-- confirming {e['start'][0]} → {e['end'][0]} --")
            conf = refine(article, con, snaps, members, present, idx_of_rev, span)
            if conf and conf[2] >= CONFIRM_DROP:
                confirmed.append((e, span))
        if confirmed:
            confirmed.sort(key=lambda x: -x[0]["abs"])
            top = confirmed[0][0]
            kind = ("recent retrofit" if top["age"] <= RECENT_YEARS
                    else f"standing distortion — persisted {top['age']:.0f}yr (a long-standing-distortion candidate)")
            print(f"\nVERDICT: PIVOT ({kind}) — {len(confirmed)} confirmed episode(s), by PWR-mass:")
            for e, _ in confirmed:
                print(f"  • {e['start'][0]} → {e['end'][0]}  (~{int(e['abs']):,} PWR, age {e['age']:.1f}yr, "
                      f"peak {e['peak']:.0f}%)  [{_recency_tag(e['age'])}]")
            for e, span in confirmed[:2]:            # attribute the two largest (by PWR-mass)
                attribute(article, con, span)
        elif mean > CREEP_MEAN:
            print(f"\nVERDICT: CREEP (elevated destruction, no single episode binary-search-confirmed)")
        else:
            print(f"\nVERDICT: HEALTHY/stable (candidate episodes not confirmed)")
    elif mean > CREEP_MEAN:
        print(f"\nVERDICT: CREEP")
    else:
        print(f"\nVERDICT: HEALTHY/stable")
    con.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Zionism")

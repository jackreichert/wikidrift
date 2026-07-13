"""L1 — the editor-agnostic, PWR-grounded drift/pivot engine (promoted from spike 005).

Pipeline (analyze):
  1. sizes    — per-revision byte size (Action API) so we can reject transient blanking when snapshotting.
  2. snapshot — at each date, a PERSISTENT revision (size ~ local median), not the last-before-date.
  3. coarse   — persistence-weighted content loss per interval (PWR); classify HEALTHY/CREEP/PIVOT.
  4. refine   — binary-search the peak interval for the exact drop revision, confirming the durable spine
                actually collapsed.
  5. attribute— WHO: destroyers (removed established spine, via each token's terminal `out`) + replacers
                (origin editors of the current post-pivot text).

Metric grounding: each token carries a persistence weight w(t) = snapshots survived since origin — a
snapshot-sampled analog of Halfaker et al.'s Persistent-Word-Revisions (WikiSym 2009) and Adler & de
Alfaro's content-survival (WWW 2007). Drift = persistence-weighted content loss; the old raw
established-deletion % is the degenerate case w≡1. NB this is a *change* detector, not a *bias* detector
(base-rate finding) — PWR makes the magnitude citable; it does not, alone, distinguish capture from a
legit rewrite.

Verdict ranking: confirmed episodes ranked by PWR-mass (age-agnostic — a long-standing distortion like
KL Warschau is a primary target, so age must NOT bury it). Recency is a DESCRIPTOR only ("recent retrofit"
vs "standing distortion"), never a demoter. A tiny old blip (Water 2007) is demoted by small MASS, not age.
"""
import statistics
import datetime as dt
from bisect import bisect_right

import duckdb

from . import config, provenance
from .corpus import Corpus
from .config import (MIN_COHORT, MIN_MATURE, MAG_FLOOR, CONFIRM_DROP,
                     CREEP_MEAN, DURABLE_Q, RECENT_YEARS, ELEVATED, SLOW_BLEED_FLOOR)


def load_membership(con, article):
    """Snapshot membership + PWR weights, computed once from rsnap (no WikiWho calls).

    Returns (snaps, members, present, idx_of_rev):
      snaps    — [(snap_date, snap_rev)] ordered in time
      members  — [set(token_id)] per snapshot index
      present  — {token_id: [snapshot indices it appears in]} (ascending -> sorted)
      idx_of_rev — {snap_rev: snapshot index}
    Weight w(t,k) = # snapshots <= k containing t (see `_pwr`)."""
    corpus = Corpus(con)
    snaps = corpus.snapshots(article)
    idx = {(sd, sr): i for i, (sd, sr) in enumerate(snaps)}
    members = [set() for _ in snaps]
    present = {}
    # One set-based query instead of one per snapshot (was N+1, ~51 queries on a decades-long article,
    # multiplied by every caller: analyze/verdict_dict/benchmark/validate/l4). Rows arrive grouped by
    # snapshot in the same (snap_date, snap_rev) order as `snaps`, so present[t] stays ascending in index.
    for sd, sr, t in corpus.membership_rows(article):
        i = idx[(sd, sr)]
        members[i].add(t)
        present.setdefault(t, []).append(i)
    idx_of_rev = {sr: i for i, (sd, sr) in enumerate(snaps)}
    return snaps, members, present, idx_of_rev


def _pwr(present, token, k):
    """Earned survival (persistent-word-snapshots) of `token` as of snapshot k."""
    return bisect_right(present[token], k)


def _intervals(snaps, members, present):
    """Yield a per-interval PWR-loss record (d0, r0, d1, r1, ratio, size, wlost, mature) for every
    snapshot interval — mature AND immature. The single source the pure `coarse` and its report share."""
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
        yield d0, r0, d1, r1, ratio, size, wlost, size >= MIN_MATURE


def coarse(snaps, members, present):
    """Per-interval persistence-weighted content loss — the PWR-grounded drift metric. PURE (no printing;
    presentation is `print_coarse_report`).

    ratio D = Σ w(t) over tokens lost in [k,k+1] / Σ w(t) over tokens present at k; absolute magnitude
    = Σ w(t) destroyed (the episode-ranking key). Returns (series, (mean, med, std)) over MATURE intervals."""
    series = [(d0, r0, d1, r1, ratio, size, wlost)
              for d0, r0, d1, r1, ratio, size, wlost, mature in _intervals(snaps, members, present) if mature]
    vals = [row[4] for row in series]
    if not vals:
        return [], (0, 0, 0)
    mean = statistics.mean(vals); med = statistics.median(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0
    return series, (mean, med, std)


def print_coarse_report(snaps, members, present):
    """Print the human-readable per-interval PWR-loss table (the presentation half of `coarse`)."""
    print(f"\n{'interval end':>12} | {'size':>7} | {'pwr_loss':>8} | {'pwr_destroyed':>13}")
    print("-" * 52)
    vals = []
    for d0, r0, d1, r1, ratio, size, wlost, mature in _intervals(snaps, members, present):
        flag = "" if mature else "  (immature — excluded)"
        bar = "#" * int(ratio / 3) if mature else ""
        print(f"{d1:>12} | {size:>7,} | {ratio:>7.1f}% | {wlost:>13,} {bar}{flag}")
        if mature:
            vals.append(ratio)
    if vals:
        mean = statistics.mean(vals); med = statistics.median(vals)
        print("-" * 52)
        print(f"persistence-weighted loss: mean {mean:.1f}%  median {med:.1f}%  peak {max(vals):.1f}%")


def build_episodes(series, elevated=ELEVATED):
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
    revs = Corpus(con).revisions_between(article, d0 + "T00:00:00Z", d1 + "T00:00:00Z")
    if len(cohort) < MIN_COHORT or len(revs) < 3:
        print("  (interval too small to refine)"); return None
    f = lambda i: len({t["token_id"] for t in provenance.tokens_at(article, revs[i][0])} & cohort) / len(cohort)
    f_start, f_end = f(0), f(len(revs) - 1)
    interval_drop = f_start - f_end   # durable-spine survival decline across the whole interval
    lo, hi, flo, fhi = 0, len(revs) - 1, f_start, f_end
    path = []
    while hi - lo > 1:
        mid = (lo + hi) // 2; fmid = f(mid); path.append((revs[mid][1][:10], fmid))
        if flo - fmid >= fmid - fhi:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    print(f"\n  binary search on durable spine |C|={len(cohort):,} (w≥{cut}): "
          f"{f_start*100:.0f}% → {f_end*100:.0f}%  (interval decline {interval_drop*100:.0f} pts)")
    print(f"  path: " + " ".join(f"{d}:{v*100:.0f}%" for d, v in path))
    print(f"  ⇒ dominant drop between rev {revs[lo][0]} ({revs[lo][1][:10]}) and rev {revs[hi][0]} ({revs[hi][1][:10]})")
    return revs[lo], revs[hi], interval_drop


def destroyers(article, con, peak):
    """WHO removed the established spine in the pivot window — {editor: tokens_removed}, and the total.

    Structured extract shared by `attribute` (prints it) and L4 graph-guided discovery (seeds from it).
    A token counts as destroyed if it was established *before* the window (origin < d0), its terminal
    `out` revision falls *inside* the window, and it is absent from the latest snapshot. One WikiWho call
    (tokens_at r0, io=True); everything else is the cached timeline.

    Returns (killers, killed, origin_ts, editor_of, latest) — the revision maps + latest-snapshot row are
    returned so `attribute` can reuse them instead of re-issuing the same full-table scans."""
    d0, r0, d1, r1, _ = peak
    corpus = Corpus(con)
    snap = provenance.tokens_at(article, r0, io=True)
    # "current tokens" = the latest snapshot we actually have (rsnap), NOT the stale `tokens` table.
    latest = corpus.latest_snap_rev(article)
    cur = corpus.snapshot_token_ids(article, latest[0]) if latest else set()
    origin_ts = corpus.revision_ts(article)
    editor_of = corpus.revision_editor(article)
    d0ts = d0 + "T00:00:00Z"; d1ts = d1 + "T00:00:00Z"
    killers = {}
    killed = 0
    for t in snap:
        o = t["o_rev_id"]; ots = origin_ts.get(o)
        if not ots or ots >= d0ts:  # not established before the interval
            continue
        outs = [x for x in t.get("out", []) if editor_of.get(x)]
        if not outs:
            continue
        death = max(outs)
        dts = origin_ts.get(death)
        if dts and d0ts < dts <= d1ts and t["token_id"] not in cur:
            killers[editor_of.get(death, "?")] = killers.get(editor_of.get(death, "?"), 0) + 1
            killed += 1
    return killers, killed, origin_ts, editor_of, latest


def attribute(article, con, peak):
    """WHO did it — destroyers (removed established spine) and replacers (wrote the new text)."""
    d0, r0, d1, r1, _ = peak
    print(f"\n  ── WHO DID IT ({d0} → {d1}) ──")
    killers, killed, origin_ts, editor_of, latest = destroyers(article, con, peak)
    d0ts = d0 + "T00:00:00Z"
    print(f"  DESTROYERS — editors who removed established-spine tokens in this window ({killed:,} tokens killed):")
    for u, n in sorted(killers.items(), key=lambda x: -x[1])[:8]:
        print(f"    {n:>6,}  {u}")
    reps = {}
    if latest:
        for tok_id, o_rev in Corpus(con).snapshot_tokens(article, latest[0]):
            ots = origin_ts.get(o_rev)
            if ots and ots > d0ts:
                u = editor_of.get(o_rev, "?"); reps[u] = reps.get(u, 0) + 1
    top = sorted(reps.items(), key=lambda x: -x[1])[:8]
    total_new = sum(n for _, n in top)
    print(f"  REPLACERS — top authors of current text written after {d0} ({total_new:,}+ tokens shown):")
    for u, n in top:
        print(f"    {n:>6,}  {u}")


def _age_years(end_date, horizon):
    """Years from an episode's end to the analysis horizon (article's last snapshot) — deterministic,
    no wall-clock. The horizon, not `now`, keeps results reproducible across re-runs."""
    return max(0.0, (dt.date.fromisoformat(horizon) - dt.date.fromisoformat(end_date)).days / 365.25)


def annotate_episodes(episodes, horizon):
    """Annotate each episode with age and RANK BY PWR-mass (age-agnostic). Recency is DESCRIPTIVE, never
    a demoter: a large *old* drift that persisted is a standing distortion (cf. KL Warschau) — still a
    find, so age must not bury it. A tiny old blip (Water 2007) is demoted by small MASS, not age."""
    for e in episodes:
        e["age"] = _age_years(e["end"][0], horizon)
    episodes.sort(key=lambda e: -e["abs"])
    return episodes


def _recency_tag(age):
    return "recent" if age <= RECENT_YEARS else f"standing {age:.0f}yr"


def _creep_or_healthy_label(mean):
    """CREEP vs HEALTHY/stable by the sustained-mean-loss threshold — the terminal L1 label shared by the
    'no episodes' and 'episodes-but-none-confirmed' branches of analyze."""
    return "CREEP" if mean > CREEP_MEAN else "HEALTHY/stable"


def ranked_episodes(con, article):
    """Shared L1 core: load snapshots, compute the coarse PWR series, and rank the above-floor episodes by
    PWR-mass. Returns (snaps, members, present, idx_of_rev, series, stats, episodes); episodes is [] when
    there are too few snapshots. Single source for verdict_dict / analyze / l4.top_episode (was open-coded
    in all three — a MAG_FLOOR/ranking change had to be repeated or they silently diverged)."""
    snaps, members, present, idx_of_rev = load_membership(con, article)
    if len(snaps) < 3:
        return snaps, members, present, idx_of_rev, [], (0, 0, 0), []
    series, stats = coarse(snaps, members, present)
    horizon = snaps[-1][0]
    episodes = annotate_episodes([e for e in build_episodes(series) if e["peak"] >= MAG_FLOOR], horizon)
    return snaps, members, present, idx_of_rev, series, stats, episodes


def _cumulative_loss_windows(series, months=12, threshold=SLOW_BLEED_FLOOR):
    """12-month rolling window slow-bleed detector: steady per-interval losses that never individually
    trigger ELEVATED (and thus never form an episode) but accumulate to a large fraction of content mass.
    ratio = sum(wlost) / peak_size across the window. Returns (start_date, end_date, ratio) of the worst
    window that exceeds threshold, or None if no window qualifies."""
    if len(series) < 2:
        return None
    delta_days = months * 30
    best = None
    for i in range(len(series)):
        d0 = dt.date.fromisoformat(series[i][0])
        cutoff = d0 + dt.timedelta(days=delta_days)
        window = [s for s in series[i:] if dt.date.fromisoformat(s[0]) <= cutoff]
        if len(window) < 2:
            continue
        peak_size = max(s[5] for s in window)
        if not peak_size:
            continue
        total_wlost = sum(s[6] for s in window)
        ratio = total_wlost / peak_size
        if best is None or ratio > best[2]:
            best = (window[0][0], window[-1][2], round(ratio, 3))
    return best if (best and best[2] >= threshold) else None


def verdict_dict(con, article):
    """Structured, machine-scorable OFFLINE verdict (no WikiWho): the coarse PWR metric, episodes ranked
    by PWR-mass with recency as a descriptor. UNCONFIRMED candidate — binary-search confirmation
    (`analyze`) is a separate precision step. Consumed by the benchmark. Necessary, not sufficient."""
    snaps, _members, _present, _idx, series, (mean, med, std), eps = ranked_episodes(con, article)
    if len(snaps) < 3:
        return {"article": article, "verdict": "SKIP", "reason": "too few snapshots", "top_mass": 0, "episodes": []}
    horizon = snaps[-1][0]
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
    slow = _cumulative_loss_windows(series)
    if slow:
        out["slow_bleed"] = {"start": slow[0], "end": slow[1], "ratio": slow[2]}
    return out


def candidate_verdict(con, article):
    """Human-readable one-liner wrapping verdict_dict (offline batch calibration)."""
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


def _concentration(counts):
    """Top-10 editors' share of authored tokens + distinct-editor count — the authorship-diversity
    descriptor (design §10.2). Pure; `counts` is {editor: tokens_authored}."""
    total = sum(counts.values())
    if not total:
        return 0.0, 0
    top10 = sum(sorted(counts.values(), reverse=True)[:10])
    return round(100.0 * top10 / total, 1), len(counts)


def profile(con, article):
    """Descriptive L1 drift profile (offline, no WikiWho) — recency + editor concentration of the CURRENT
    text, from the latest cached snapshot joined to the revision timeline. Promoted from spike 002; a
    context signal / LEAD (high recency + high concentration ⇒ a drift lead), never a verdict."""
    corpus = Corpus(con)
    latest = corpus.latest_snapshot(article)
    if not latest:
        return {"article": article, "reason": "no snapshots"}
    snap_date, snap_rev = latest
    rows = corpus.snapshot_o_rev_ids(article, snap_rev)
    ts_of = corpus.revision_ts(article)
    user_of = corpus.revision_editor(article)
    horizon = dt.date.fromisoformat(snap_date)
    ages, per_editor, recent = [], {}, 0
    for (o,) in rows:
        ts = ts_of.get(o)
        if ts:
            age = (horizon - dt.date.fromisoformat(ts[:10])).days / 365.25
            ages.append(age)
            if age <= RECENT_YEARS:
                recent += 1
        per_editor[user_of.get(o, "?")] = per_editor.get(user_of.get(o, "?"), 0) + 1
    n = len(rows)
    top10_share, n_editors = _concentration(per_editor)
    return {"article": article, "horizon": snap_date, "n_tokens": n,
            "median_age_yrs": round(statistics.median(ages), 1) if ages else 0.0,
            "pct_recent": round(100.0 * recent / n, 1) if n else 0.0, "recent_years": RECENT_YEARS,
            "top10_editor_share": top10_share, "distinct_editors": n_editors}


def profile_report(article, con=None, persist=True):
    """Print the descriptive drift profile for one article (offline); persist a viewer-shaped findings file."""
    owns = con is None
    if owns:
        con = duckdb.connect(str(config.DB), read_only=True)
    p = profile(con, article)
    if owns:
        con.close()
    if p.get("reason"):
        print(f"{article}: {p['reason']}")
        return p
    if persist:
        config.write_findings(f"{config.slugify(article)}.profile.json", p)
    print(f"=== L1 drift profile — {article} (as of {p['horizon']}) ===")
    print(f"  current text            : {p['n_tokens']:,} tokens")
    print(f"  median age of current text: {p['median_age_yrs']} yrs")
    print(f"  authored within last {p['recent_years']:.0f}yr: {p['pct_recent']}%")
    print(f"  editor concentration    : top-10 editors = {p['top10_editor_share']}% of current text; "
          f"{p['distinct_editors']} distinct editors")
    print("  (descriptive context — high recency + high concentration is a drift LEAD, not a verdict.)")
    return p


def analyze(article, con=None):
    """Full L1 pipeline for one article, with confirmation + attribution (prints a report).

    Fetches sizes + snapshots (WikiWho/Action) as needed, then classifies. This is the confirmed
    path (binary search); the offline candidate path is `verdict_dict`."""
    owns = con is None
    if owns:
        con = duckdb.connect(str(config.DB))
    print(f"=== ANALYZE: {article} ===", flush=True)
    provenance.ensure_sizes(con, article)
    provenance.ensure_indexes(con)
    provenance.build_snapshots(con, article)
    snaps, members, present, idx_of_rev, series, (mean, med, std), episodes = ranked_episodes(con, article)
    if len(snaps) < 3:
        print("  too few snapshots to analyze")
        if owns: con.close()
        return
    print(f"  {len(snaps)} persistent snapshots {snaps[0][0]}..{snaps[-1][0]}", flush=True)
    print_coarse_report(snaps, members, present)

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
        else:                                        # candidate episodes existed but none binary-search-confirmed
            nuance = ("elevated destruction, no single episode binary-search-confirmed"
                      if mean > CREEP_MEAN else "candidate episodes not confirmed")
            print(f"\nVERDICT: {_creep_or_healthy_label(mean)} ({nuance})")
    else:                                            # no candidate episodes at all
        print(f"\nVERDICT: {_creep_or_healthy_label(mean)}")
    if owns:
        con.close()

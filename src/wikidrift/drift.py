"""L1 — the editor-agnostic, PWR-grounded drift/pivot engine (promoted from spike 005).

Pipeline (analyze):
  1. sizes    — per-revision byte size (Action API) so we can reject transient blanking when snapshotting.
  2. snapshot — at each date, a PERSISTENT revision (size ~ local median), not the last-before-date.
  3. coarse   — persistence-weighted content loss per interval (PWR); classify HEALTHY/CREEP/PIVOT.
  4. refine   — binary-search the peak interval for the exact drop revision, confirming the durable spine
                actually collapsed.
    5. attribute— removal attribution (via each token's terminal `out`) + post-pivot contributors
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

from . import config, event_attribution as event_ledger, process_context, provenance
from .corpus import Corpus
from .config import (MIN_COHORT, MIN_MATURE, MAG_FLOOR, CONFIRM_DROP,
                     CREEP_MEAN, DURABLE_Q, RECENT_YEARS, ELEVATED,
                     GAIN_FLOOR, REPLACEMENT_FLOOR, EXTREME_CHANGE, REVIEW_MASS_FLOOR,
                     MASS_FLOOR, ROLLING_WINDOW_MONTHS, ROLLING_TOLERANCE_DAYS, ROLLING_DROP)

CONFIRMATION_SCHEMA_VERSION = 2


def confirmation_name(article):
    return f"{config.slugify(article)}.l1-confirmation.json"


def load_confirmation(article):
    """Load the last full-analysis confirmation artifact for an article, if one exists."""
    return config.load_findings(confirmation_name(article))


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


def _coverage_plan(expected_snapshots, loaded_snapshots):
    """Describe missing scheduled snapshots and intervals that cross those evidence gaps."""
    loaded_revids = {revid for _date, revid in loaded_snapshots}
    missing = [
        {"date": date, "revid": revid}
        for date, revid in expected_snapshots
        if revid not in loaded_revids
    ]
    missing_dates = {item["date"] for item in missing}
    excluded = {
        (start_revid, end_revid)
        for (start_date, start_revid), (end_date, end_revid)
        in zip(loaded_snapshots, loaded_snapshots[1:])
        if any(start_date < missing_date < end_date for missing_date in missing_dates)
    }
    expected_revids = {revid for _date, revid in expected_snapshots}
    endpoints_complete = bool(expected_snapshots) and {
        expected_snapshots[0][1], expected_snapshots[-1][1],
    }.issubset(loaded_revids)
    return {
        "status": "partial" if expected_revids - loaded_revids else "complete",
        "missing_snapshots": missing,
        "excluded_intervals": excluded,
        "endpoints_complete": endpoints_complete,
    }


def _pwr(present, token, k):
    """Earned survival (persistent-word-snapshots) of `token` as of snapshot k."""
    return bisect_right(present[token], k)


def _candidate_priority(percentages, masses):
    """Rank urgency; high mass or an extreme percentage independently earns high priority."""
    if max(masses, default=0) >= MASS_FLOOR or max(percentages, default=0) >= EXTREME_CHANGE:
        return "high"
    if max(masses, default=0) >= REVIEW_MASS_FLOOR:
        return "review"
    return "low"


def _interval_measurements(snaps, members, present):
    """Measure retained, removed, and standing-added PWR for every snapshot interval."""
    for index in range(len(snaps) - 1):
        start_date, start_revid = snaps[index]
        end_date, end_revid = snaps[index + 1]
        at_start, at_end = members[index], members[index + 1]
        if not at_start:
            continue
        removed = at_start - at_end
        added = at_end - at_start
        retained = at_start & at_end
        start_weight = sum(_pwr(present, token, index) for token in at_start)
        removed_weight = sum(_pwr(present, token, index) for token in removed)
        added_weight = sum(_pwr(present, token, index + 1) for token in added)
        retained_weight = sum(_pwr(present, token, index) for token in retained)
        loss_pct = 100.0 * removed_weight / start_weight if start_weight else 0.0
        gain_pct = 100.0 * added_weight / start_weight if start_weight else 0.0
        retained_pct = 100.0 * retained_weight / start_weight if start_weight else 0.0
        replacement_weight = min(removed_weight, added_weight)
        replacement_pct = 100.0 * replacement_weight / start_weight if start_weight else 0.0
        anomaly_types = []
        if loss_pct >= ELEVATED:
            anomaly_types.append("loss")
        if gain_pct >= GAIN_FLOOR:
            anomaly_types.append("gain")
        if replacement_pct >= REPLACEMENT_FLOOR:
            anomaly_types.append("replacement")
        yield {
            "start": start_date,
            "start_revid": start_revid,
            "end": end_date,
            "end_revid": end_revid,
            "size": len(at_start),
            "pwr_loss": loss_pct,
            "pwr_removed": removed_weight,
            "pwr_gain": gain_pct,
            "pwr_added": added_weight,
            "pwr_retained": retained_pct,
            "replacement_candidate": replacement_pct,
            "replacement_pwr": replacement_weight,
            "confirmable": len(at_start) >= MIN_MATURE,
            "anomaly_types": anomaly_types,
            "priority": _candidate_priority(
                [loss_pct, gain_pct, replacement_pct],
                [removed_weight, added_weight, replacement_weight],
            ),
        }


def _intervals(snaps, members, present):
    """Yield a per-interval PWR-loss record (d0, r0, d1, r1, ratio, size, wlost, mature) for every
    snapshot interval — mature AND immature. The single source the pure `coarse` and its report share."""
    for measurement in _interval_measurements(snaps, members, present):
        yield (
            measurement["start"], measurement["start_revid"],
            measurement["end"], measurement["end_revid"],
            measurement["pwr_loss"], measurement["size"],
            measurement["pwr_removed"], measurement["confirmable"],
        )


def coarse(snaps, members, present, excluded_intervals=None):
    """Per-interval persistence-weighted content loss — the PWR-grounded drift metric. PURE (no printing;
    presentation is `print_coarse_report`).

    ratio D = Σ w(t) over tokens lost in [k,k+1] / Σ w(t) over tokens present at k; absolute magnitude
    = Σ w(t) removed (the episode-ranking key). Returns (series, (mean, med, std)) over MATURE intervals."""
    excluded_intervals = excluded_intervals or set()
    series = [(d0, r0, d1, r1, ratio, size, wlost)
              for d0, r0, d1, r1, ratio, size, wlost, mature in _intervals(snaps, members, present)
              if mature and (r0, r1) not in excluded_intervals]
    vals = [row[4] for row in series]
    if not vals:
        return [], (0, 0, 0)
    mean = statistics.mean(vals); med = statistics.median(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0
    return series, (mean, med, std)


def print_coarse_report(snaps, members, present, excluded_intervals=None):
    """Print the human-readable per-interval PWR-loss table (the presentation half of `coarse`)."""
    excluded_intervals = excluded_intervals or set()
    print(f"\n{'interval end':>12} | {'size':>7} | {'pwr_loss':>8} | {'pwr_removed':>13}")
    print("-" * 52)
    vals = []
    for d0, r0, d1, r1, ratio, size, wlost, mature in _intervals(snaps, members, present):
        covered = (r0, r1) not in excluded_intervals
        flag = (
            "  (coverage gap — excluded)" if not covered
            else "" if mature
            else "  (immature — excluded)"
        )
        bar = "#" * int(ratio / 3) if mature and covered else ""
        print(f"{d1:>12} | {size:>7,} | {ratio:>7.1f}% | {wlost:>13,} {bar}{flag}")
        if mature and covered:
            vals.append(ratio)
    if vals:
        mean = statistics.mean(vals); med = statistics.median(vals)
        print("-" * 52)
        print(f"persistence-weighted loss: mean {mean:.1f}%  median {med:.1f}%  peak {max(vals):.1f}%")


def _coarse_profile(snaps, members, present, excluded_intervals=None):
    """Serialize the terminal PWR interval series for downstream visualizations."""
    excluded_intervals = excluded_intervals or set()
    profile = []
    for measurement in _interval_measurements(snaps, members, present):
        confirmable = measurement["confirmable"]
        profile.append({
            "start": measurement["start"],
            "end": measurement["end"],
            "size": measurement["size"],
            "pwr_loss": round(measurement["pwr_loss"], 2),
            "pwr_removed": int(measurement["pwr_removed"]),
            "pwr_gain": round(measurement["pwr_gain"], 2),
            "pwr_added": int(measurement["pwr_added"]),
            "pwr_retained": round(measurement["pwr_retained"], 2),
            "replacement_candidate": round(measurement["replacement_candidate"], 2),
            "replacement_pwr": int(measurement["replacement_pwr"]),
            "confirmable": confirmable,
            "mature": confirmable,  # Legacy artifact alias; new consumers should use confirmable.
            "eligible": (
                measurement["start_revid"], measurement["end_revid"]
            ) not in excluded_intervals,
            "anomaly_types": measurement["anomaly_types"],
            "priority": measurement["priority"],
        })
    return profile


def _sweep_candidates(interval_profile):
    """Persist every covered anomaly; confidence and mass affect labels, never inclusion."""
    candidates = []
    evidence_fields = (
        "pwr_loss", "pwr_removed", "pwr_gain", "pwr_added",
        "replacement_candidate", "replacement_pwr",
    )
    for interval in interval_profile:
        anomaly_types = interval.get("anomaly_types") or []
        if not anomaly_types or interval.get("eligible", True) is False:
            continue
        candidate = {
            "start": interval.get("start"),
            "end": interval.get("end"),
            "anomaly_types": list(anomaly_types),
            "priority": interval.get("priority", "low"),
            "decision": (
                "pending_confirmation" if interval.get("confirmable", interval.get("mature", False))
                else "descriptive_only"
            ),
        }
        candidate.update({field: interval.get(field, 0) for field in evidence_fields})
        candidates.append(candidate)
    return candidates


def build_episodes(series, elevated=ELEVATED):
    """Group time-contiguous intervals with persistence-weighted loss >= `elevated` into episodes.
    `abs` accumulates PWR-mass removed (the ranking key); `peak` is the max interval loss %."""
    episodes, cur = [], None
    for d0, r0, d1, r1, ratio, size, absd in series:
        if ratio >= elevated:
            if cur and cur["end"][0] == d0:                 # time-contiguous with the running episode
                cur["end"] = (d1, r1); cur["abs"] += absd; cur["peak"] = max(cur["peak"], ratio)
            else:
                if cur: episodes.append(cur)
                cur = {"start": (d0, r0), "end": (d1, r1), "abs": absd, "peak": ratio,
                       "source": "interval"}
        elif cur:
            episodes.append(cur); cur = None
    if cur: episodes.append(cur)
    return episodes


def rolling_candidates(snaps, members, present, months=ROLLING_WINDOW_MONTHS,
                       tolerance_days=ROLLING_TOLERANCE_DAYS, threshold=ROLLING_DROP,
                       mass_floor=0, min_mature=MIN_MATURE, blocked_dates=None):
    """Find direct weighted cohort loss near the target window length.

    This second pass catches sustained medium loss that does not cross the high-precision threshold in
    any single snapshot interval. Sparse histories without a snapshot inside the tolerance are skipped.
    """
    candidates = []
    blocked_dates = {dt.date.fromisoformat(date) for date in (blocked_dates or set())}
    target_days = round(months * 365.25 / 12)
    snap_dates = [dt.date.fromisoformat(snap[0]) for snap in snaps]
    for start in range(len(snaps) - 1):
        start_date = snap_dates[start]
        eligible = [
            end for end in range(start + 1, len(snaps))
            if abs((snap_dates[end] - start_date).days - target_days) <= tolerance_days
        ]
        if not eligible or len(members[start]) < min_mature:
            continue
        end = min(eligible, key=lambda index: abs((snap_dates[index] - start_date).days - target_days))
        if any(start_date < blocked_date < snap_dates[end] for blocked_date in blocked_dates):
            continue
        at_start, at_end = members[start], members[end]
        total_weight = sum(_pwr(present, token, start) for token in at_start)
        lost_weight = sum(_pwr(present, token, start) for token in at_start - at_end)
        loss_pct = 100.0 * lost_weight / total_weight if total_weight else 0.0
        if loss_pct >= threshold and lost_weight >= mass_floor:
            candidates.append({
                "start": snaps[start], "end": snaps[end], "abs": lost_weight,
                "peak": loss_pct, "source": "rolling",
            })
    return annotate_episodes(candidates, snaps[-1][0]) if snaps else []


def non_overlapping_candidates(candidates, blocked=()):
    """Keep the highest-PWR candidate from each overlapping time range."""
    selected = []

    def overlaps(left, right):
        return left["start"][0] < right["end"][0] and right["start"][0] < left["end"][0]

    for candidate in sorted(candidates, key=lambda item: -item["abs"]):
        if any(overlaps(candidate, other) for other in (*blocked, *selected)):
            continue
        selected.append(candidate)
    return selected


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


def removal_attribution(article, con, peak):
    """Attribute established-token removals in the pivot window — {editor: tokens_removed}, and the total.

    Structured extract shared by `attribute` (prints it) and L4 graph-guided discovery (seeds from it).
    A token counts as removed if it was established *before* the window (origin < d0), its terminal
    `out` revision falls *inside* the window, and it is absent from the latest snapshot. One WikiWho call
    (tokens_at r0, io=True); everything else is the cached timeline.

    Returns (removals_by_editor, removed_count, origin_ts, editor_of, latest) — the revision maps and
    latest-snapshot row are returned so `attribute` can reuse them instead of re-issuing the same scans."""
    d0, r0, d1, r1, _ = peak
    corpus = Corpus(con)
    snap = provenance.tokens_at(article, r0, io=True)
    # "current tokens" = the latest snapshot we actually have (rsnap), NOT the stale `tokens` table.
    latest = corpus.latest_snap_rev(article)
    cur = corpus.snapshot_token_ids(article, latest[0]) if latest else set()
    origin_ts = corpus.revision_ts(article)
    editor_of = corpus.revision_editor(article)
    d0ts = d0 + "T00:00:00Z"; d1ts = d1 + "T00:00:00Z"
    removals_by_editor = {}
    removed_count = 0
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
            editor = editor_of.get(death, "?")
            removals_by_editor[editor] = removals_by_editor.get(editor, 0) + 1
            removed_count += 1
    return removals_by_editor, removed_count, origin_ts, editor_of, latest


def _timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def event_attribution(article, con, episode):
    """Return a multi-revision activity ledger for one exact event between stable boundaries."""
    before_revid = episode["before_revid"]
    after_revid = episode["after_revid"]
    before_timestamp = episode["before_timestamp"]
    after_timestamp = episode["after_timestamp"]
    corpus = Corpus(con)
    revisions = corpus.revision_evidence_between(article, before_timestamp, after_timestamp)
    revision_ids = [revision["revision_id"] for revision in revisions]
    if before_revid not in revision_ids:
        raise ValueError(
            f"{article!r} event before-boundary revision {before_revid} is absent from the timeline"
        )
    if after_revid not in revision_ids:
        raise ValueError(
            f"{article!r} event after-boundary revision {after_revid} is absent from the timeline"
        )
    before_index = revision_ids.index(before_revid)
    after_index = revision_ids.index(after_revid)
    if before_index >= after_index:
        raise ValueError(f"{article!r} event boundaries are not in chronological revision order")
    revisions = revisions[before_index:after_index + 1]
    for revision in revisions:
        revision["tokens"] = provenance.tokens_at(article, revision["revision_id"])

    result = event_ledger.attribute_revision_sequence(article, revisions)
    result.update({
        "duration_seconds": int((_timestamp(after_timestamp) - _timestamp(before_timestamp)).total_seconds()),
    })
    return result


def attribute(article, con, episode, render=True):
    """Calculate exact-event attribution and optionally render its neutral terminal receipt."""
    result = event_attribution(article, con, episode)
    if not render:
        return result
    print(f"\n  ── ATTRIBUTION (rev {result['before_revid']} → {result['after_revid']}) ──")
    print(
        "  REMOVALS — editors associated with terminal removals in this exact event "
        f"({result['removed_tokens']:,} tokens removed):"
    )
    for row in result["removals_by_editor"][:8]:
        print(f"    {row['tokens']:>6,}  {row['editor']}")
    print(
        "  REPLACEMENT — origin authors of surviving replacement text in this exact event "
        f"({result['replacement_tokens']:,} tokens):"
    )
    for row in result["replacement_by_editor"][:8]:
        print(f"    {row['tokens']:>6,}  {row['editor']}")
    return result


def _validate_confirmation_corpus(con, article, confirmation):
    """Require an artifact produced for the current corpus and threshold contract."""
    schema_version = confirmation.get("schema_version", 1)
    if schema_version > CONFIRMATION_SCHEMA_VERSION:
        raise ValueError(
            f"{article!r} confirmation schema version {schema_version} is newer than supported "
            f"version {CONFIRMATION_SCHEMA_VERSION}"
        )
    if (confirmation.get("thresholds") or {}) != config.confirmation_thresholds():
        raise ValueError(f"{article!r} confirmation threshold contract is stale")
    current_horizon = Corpus(con).latest_snapshot(article)
    saved_horizon = confirmation.get("corpus_horizon") or {}
    saved = (saved_horizon.get("snapshot_date"), saved_horizon.get("snapshot_revid"))
    if not current_horizon or saved != tuple(current_horizon):
        raise ValueError(f"{article!r} confirmation corpus horizon is stale")


def _validate_confirmation_for_backfill(con, article, confirmation):
    """Require a confirmed artifact produced for the current corpus and threshold contract."""
    _validate_confirmation_corpus(con, article, confirmation)
    if confirmation.get("status") != "confirmed":
        raise ValueError(f"{article!r} has no confirmed attribution target")
    if not confirmation.get("confirmed_episodes"):
        raise ValueError(f"{article!r} confirmation has no exact episodes")


def backfill_interval_profile(article, con=None, persist=True, force=False):
    """Add interval-level PWR receipts to a current confirmation without rerunning L1."""
    owns_connection = con is None
    if owns_connection:
        con = duckdb.connect(str(config.DB), read_only=True)
    try:
        confirmation = load_confirmation(article)
        _validate_confirmation_corpus(con, article, confirmation)
        existing_profile = confirmation.get("interval_profile")
        if existing_profile is not None and not force:
            return {
                "article": article,
                "interval_count": len(existing_profile),
                "skipped": True,
            }

        snaps, members, present, _idx_of_rev = load_membership(con, article)
        confirmation["interval_profile"] = _coarse_profile(snaps, members, present)
        confirmation["sweep_candidates"] = _sweep_candidates(confirmation["interval_profile"])
        confirmation["sweep_thresholds"] = config.sweep_thresholds()
        confirmation["interval_profile_backfill_ts"] = dt.datetime.now(dt.timezone.utc).isoformat()

        if persist:
            config.write_findings(confirmation_name(article), confirmation)
        return {
            "article": article,
            "interval_count": len(confirmation["interval_profile"]),
            "skipped": False,
        }
    finally:
        if owns_connection:
            con.close()


def backfill_attribution(article, con=None, persist=True, force=False):
    """Add exact-event attribution to a current confirmation artifact without rerunning L1."""
    owns_connection = con is None
    if owns_connection:
        con = duckdb.connect(str(config.DB), read_only=True)
    try:
        confirmation = load_confirmation(article)
        _validate_confirmation_for_backfill(con, article, confirmation)
        updated = 0
        skipped = 0
        failed = 0
        changed = confirmation.get("schema_version") != CONFIRMATION_SCHEMA_VERSION
        confirmation["schema_version"] = CONFIRMATION_SCHEMA_VERSION
        for episode in confirmation["confirmed_episodes"]:
            if episode.get("attribution") and not force:
                skipped += 1
                continue
            try:
                attribution = event_attribution(article, con, episode)
                episode["duration_seconds"] = attribution["duration_seconds"]
                episode["attribution"] = attribution
                episode.pop("attribution_unavailable", None)
                updated += 1
            except Exception as exc:  # noqa: BLE001
                episode["attribution"] = None
                episode["attribution_unavailable"] = str(exc)
                failed += 1
            changed = True
        if changed:
            confirmation["attribution_backfill_ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
            if persist:
                config.write_findings(confirmation_name(article), confirmation)
        return {
            "article": article,
            "updated_episodes": updated,
            "skipped_episodes": skipped,
            "failed_episodes": failed,
        }
    finally:
        if owns_connection:
            con.close()


def backfill_process_context(article, con=None, persist=True, force=False):
    """Attach neutral process receipts to current exact episodes without rerunning L1."""
    owns_connection = con is None
    if owns_connection:
        con = duckdb.connect(str(config.DB), read_only=True)
    try:
        confirmation = load_confirmation(article)
        _validate_confirmation_for_backfill(con, article, confirmation)
        updated = 0
        skipped = 0
        failed = 0
        for episode in confirmation["confirmed_episodes"]:
            if episode.get("process_context") and not force:
                skipped += 1
                continue
            try:
                episode["process_context"] = process_context.retrieve_process_context(article, episode)
                episode.pop("process_context_unavailable", None)
                updated += 1
            except Exception as exc:  # noqa: BLE001 - preserve per-episode availability
                episode["process_context"] = None
                episode["process_context_unavailable"] = str(exc)
                failed += 1
        if updated or failed:
            confirmation["process_context_backfill_ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
            if persist:
                config.write_findings(confirmation_name(article), confirmation)
        return {
            "article": article,
            "updated_episodes": updated,
            "skipped_episodes": skipped,
            "failed_episodes": failed,
        }
    finally:
        if owns_connection:
            con.close()


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


def ranked_episodes(con, article, excluded_intervals=None):
    """Shared L1 core: load snapshots, compute the coarse PWR series, and rank the above-floor episodes by
    PWR-mass. Returns (snaps, members, present, idx_of_rev, series, stats, episodes); episodes is [] when
    there are too few snapshots. Single source for verdict_dict / analyze / l4.top_episode (was open-coded
    in all three — a MAG_FLOOR/ranking change had to be repeated or they silently diverged)."""
    snaps, members, present, idx_of_rev = load_membership(con, article)
    if len(snaps) < 3:
        return snaps, members, present, idx_of_rev, [], (0, 0, 0), []
    series, stats = coarse(snaps, members, present, excluded_intervals)
    horizon = snaps[-1][0]
    episodes = annotate_episodes([e for e in build_episodes(series) if e["peak"] >= MAG_FLOOR], horizon)
    return snaps, members, present, idx_of_rev, series, stats, episodes


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


def _candidate_evaluation(candidate, confirmation):
    """Build an audit receipt for one candidate sent through exact checking."""
    evaluation = {
        "candidate_start": candidate["start"][0],
        "candidate_end": candidate["end"][0],
        "candidate_before_revid": candidate["start"][1],
        "candidate_after_revid": candidate["end"][1],
        "source": candidate.get("source", "interval"),
        "pwr_mass": int(candidate["abs"]),
        "peak_pct": round(candidate["peak"], 2),
    }
    if confirmation is None:
        return {
            **evaluation,
            "decision": "rejected",
            "rejection_reason": "insufficient_revision_evidence",
        }

    before, after, decline = confirmation
    confirmed = decline >= CONFIRM_DROP
    return {
        **evaluation,
        "exact_before_revid": before[0],
        "exact_before_timestamp": before[1],
        "exact_after_revid": after[0],
        "exact_after_timestamp": after[1],
        "durable_spine_drop": round(decline, 6),
        "decision": "confirmed" if confirmed else "rejected",
        "rejection_reason": None if confirmed else "durable_spine_drop_below_threshold",
    }


def _confirmed_episode(evaluation):
    """Translate a successful candidate receipt into the established episode shape."""
    return {
        "candidate_start": evaluation["candidate_start"],
        "candidate_end": evaluation["candidate_end"],
        "candidate_before_revid": evaluation["candidate_before_revid"],
        "candidate_after_revid": evaluation["candidate_after_revid"],
        "before_revid": evaluation["exact_before_revid"],
        "before_timestamp": evaluation["exact_before_timestamp"],
        "after_revid": evaluation["exact_after_revid"],
        "after_timestamp": evaluation["exact_after_timestamp"],
        "durable_spine_drop": evaluation["durable_spine_drop"],
        "pwr_mass": evaluation["pwr_mass"],
        "peak_pct": evaluation["peak_pct"],
        "source": evaluation["source"],
        "status": "confirmed",
    }


def analyze(article, con=None, persist=True):
    """Full L1 pipeline for one article, with confirmation + attribution.

    Fetches sizes + snapshots (WikiWho/Action) as needed, then classifies. This is the confirmed
    path (binary search); the offline candidate path is `verdict_dict`. Prints a report, returns a
    structured result, and persists it by default for downstream instruments."""
    owns = con is None
    if owns:
        resolved = provenance.resolve_article_title(article)
        article = resolved.canonical_title
        con = duckdb.connect(str(config.DB))
        provenance.record_article_identity(con, resolved)
    print(f"=== ANALYZE: {article} ===", flush=True)
    provenance.ensure_sizes(con, article)
    provenance.ensure_indexes(con)
    provenance.build_snapshots(con, article)
    source_state = provenance.load_source_state(con, article)
    if (source_state or {}).get("source_status") == "unavailable":
        horizon = Corpus(con).latest_snapshot(article)
        result = {
            "schema_version": CONFIRMATION_SCHEMA_VERSION,
            "article": article,
            "run_ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "corpus_horizon": {
                "snapshot_date": horizon[0], "snapshot_revid": horizon[1],
            } if horizon else None,
            "thresholds": config.confirmation_thresholds(),
            "sweep_thresholds": config.sweep_thresholds(),
            "coarse_verdict": "UNAVAILABLE",
            "status": "unavailable",
            "reason": source_state.get("reason") or "source coverage is incomplete",
            "source_state": source_state,
            "confirmed_episodes": [],
            "evaluated_candidates": [],
            "interval_profile": [],
        }
        print(f"  unavailable: {result['reason']}")
        if persist:
            config.write_findings(confirmation_name(article), result)
        if owns:
            con.close()
        return result
    analysis = ranked_episodes(con, article)
    coverage = (
        _coverage_plan(provenance.snapshot_picks(con, article), analysis[0])
        if (source_state or {}).get("source_status") == "partial"
        else {
            "status": "complete",
            "missing_snapshots": [],
            "excluded_intervals": set(),
            "endpoints_complete": bool(analysis[0]),
        }
    )
    excluded_intervals = coverage["excluded_intervals"]
    if excluded_intervals:
        analysis = ranked_episodes(con, article, excluded_intervals=excluded_intervals)
    snaps, members, present, idx_of_rev, series, (mean, med, std), episodes = analysis
    interval_profile = _coarse_profile(snaps, members, present, excluded_intervals)
    result = {
        "schema_version": CONFIRMATION_SCHEMA_VERSION,
        "article": article,
        "run_ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "corpus_horizon": {
            "snapshot_date": snaps[-1][0], "snapshot_revid": snaps[-1][1],
        } if snaps else None,
        "thresholds": config.confirmation_thresholds(),
        "sweep_thresholds": config.sweep_thresholds(),
        "source_state": source_state,
        "coverage_status": coverage["status"],
        "coverage_endpoints_complete": coverage["endpoints_complete"],
        "missing_snapshots": coverage["missing_snapshots"],
        "excluded_intervals": [list(interval) for interval in sorted(excluded_intervals)],
        "coarse_verdict": "SKIP" if len(snaps) < 3 or not series else (
            "PIVOT?" if episodes else "CREEP?" if mean > CREEP_MEAN else "HEALTHY"
        ),
        "status": "unavailable" if len(snaps) < 3 or not series else "not_confirmed",
        "confirmed_episodes": [],
        "evaluated_candidates": [],
        "interval_profile": interval_profile,
        "sweep_candidates": _sweep_candidates(interval_profile),
    }
    if result["sweep_candidates"] and not episodes:
        result["coarse_verdict"] = "ANOMALY?"
    if len(snaps) >= 3 and not series and result["sweep_candidates"]:
        result["status"] = "descriptive_anomalies"
        result["reason"] = "anomalies are below the exact-check floor"
        print(f"  {result['reason']}")
        if persist:
            config.write_findings(confirmation_name(article), result)
        if owns:
            con.close()
        return result
    if len(snaps) < 3 or not series:
        result["reason"] = "too few snapshots" if len(snaps) < 3 else "no mature covered intervals"
        print(f"  {result['reason']}")
        if persist:
            config.write_findings(confirmation_name(article), result)
        if owns: con.close()
        return result
    print(f"  {len(snaps)} persistent snapshots {snaps[0][0]}..{snaps[-1][0]}", flush=True)
    print_coarse_report(snaps, members, present, excluded_intervals)

    confirmed = []
    if episodes:
        print(f"\ncandidate pivot episodes (ranked by PWR-mass removed; recency = context, NOT a demoter):")
        for e in episodes:
            print(f"  {e['start'][0]} → {e['end'][0]}   peak {e['peak']:.0f}%   ~{int(e['abs']):,} PWR   "
                  f"age {e['age']:.1f}yr  [{_recency_tag(e['age'])}]")
        for e in episodes:
            span = (e["start"][0], e["start"][1], e["end"][0], e["end"][1], e["peak"])
            print(f"\n-- confirming {e['start'][0]} → {e['end'][0]} --")
            conf = refine(article, con, snaps, members, present, idx_of_rev, span)
            evaluation = _candidate_evaluation(e, conf)
            result["evaluated_candidates"].append(evaluation)
            if evaluation["decision"] == "confirmed":
                confirmed.append((e, span, _confirmed_episode(evaluation)))

    if not confirmed:
        rolling = non_overlapping_candidates(
            rolling_candidates(
                snaps, members, present,
                blocked_dates={item["date"] for item in coverage["missing_snapshots"]},
            ),
            blocked=episodes,
        )
        if rolling:
            print("\nrolling second-pass candidates (12-month weighted loss):")
        for e in rolling:
            span = (e["start"][0], e["start"][1], e["end"][0], e["end"][1], e["peak"])
            print(f"\n-- confirming rolling {e['start'][0]} → {e['end'][0]} "
                  f"({e['peak']:.0f}%, ~{int(e['abs']):,} PWR) --")
            conf = refine(article, con, snaps, members, present, idx_of_rev, span)
            evaluation = _candidate_evaluation(e, conf)
            result["evaluated_candidates"].append(evaluation)
            if evaluation["decision"] == "confirmed":
                confirmed.append((e, span, _confirmed_episode(evaluation)))

    if confirmed:
        confirmed.sort(key=lambda item: -item[0]["abs"])
        top = confirmed[0][0]
        result["status"] = "confirmed"
        kind = ("recent retrofit" if top["age"] <= RECENT_YEARS
                else f"standing distortion — persisted {top['age']:.0f}yr (a long-standing-distortion candidate)")
        print(f"\nVERDICT: PIVOT ({kind}) — {len(confirmed)} confirmed episode(s), by PWR-mass:")
        for e, _, record in confirmed:
            print(f"  • {e['start'][0]} → {e['end'][0]}  (~{int(e['abs']):,} PWR, age {e['age']:.1f}yr, "
                  f"peak {e['peak']:.0f}%, {record['source']})  [{_recency_tag(e['age'])}]")
        for index, (_episode, _span, record) in enumerate(confirmed):
            try:
                attribution = attribute(article, con, record, render=index < 2)
                if not isinstance(attribution, dict):
                    raise ValueError("structured attribution was not returned")
                record["duration_seconds"] = attribution["duration_seconds"]
                record["attribution"] = attribution
            except Exception as exc:  # noqa: BLE001
                record["duration_seconds"] = int(
                    (_timestamp(record["after_timestamp"]) - _timestamp(record["before_timestamp"])).total_seconds()
                )
                record["attribution"] = None
                record["attribution_unavailable"] = str(exc)
        result["confirmed_episodes"] = [record for _, _, record in confirmed]
    elif episodes:
        nuance = ("elevated destruction, no episode binary-search-confirmed"
                  if mean > CREEP_MEAN else "candidate episodes not confirmed")
        print(f"\nVERDICT: ANOMALIES UNCONFIRMED ({nuance})")
    else:
        print(f"\nVERDICT: {_creep_or_healthy_label(mean)}")
    if persist:
        config.write_findings(confirmation_name(article), result)
    if owns:
        con.close()
    return result

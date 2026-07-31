"""L1 → L2 orchestration for one article, following the pre-rank router.

Closes the methodology's "adjudicate the routed leads" gap: `prerank` raises `addition→L2` / `churn→L2`
for reframe-by-addition/churn (vectors the removal metric is structurally blind to), but nothing consumed
them — L2 had to be run by hand. This pipeline chains the layers and, when the router routes to L2,
actually runs L2 on that article.

Layers by cost (so the default stays offline and keyless):
  L1 drift + pre-rank router — offline (cached corpus); always run.
    L2.5 lexical drift         — offline term-distribution lead; always run.
  M-score corroborator       — Action API, no LLM; opt-in (`--mscore`).
  L2 stance                  — LLM (needs an LLM key; default Anthropic, --provider/--model/--base-url for a
                               cheaper/local backend); opt-in (`--llm`).

Cross-language stance comparison and fact divergence are available as standalone instruments
(`wikidrift crosslingual` / `wikidrift factcheck`) but are not part of the pipeline — they address
born-biased detection (a separate problem from drift/pivot detection) and add significant cost and
language-selection complexity. See METHODOLOGY.md §7 for the known-limits note.

Every output is a LEAD for a researcher, never a published verdict.
"""
import datetime as dt

import duckdb

from . import config, drift, prerank, stance, lexical, mscore, provenance
from .corpus import Corpus


SOURCE_CHECK_MAX_AGE = dt.timedelta(days=7)


def _snap_count(con, article):
    return Corpus(con).snapshot_count(article)


def _pipeline_entities(article, l2_summary):
    """Self-determined entity focus for pipeline orchestration.

    Prefer entities actually used by L2 in this run; otherwise fall back to the article title itself.
    This keeps pipeline routing controversy-agnostic and avoids curated topic priors in the default path.
    """
    ents = list((l2_summary or {}).get("entities") or [])
    if ents:
        return ents
    title = (article or "").strip()
    return [title] if title else []


def _pivot_window(verdict):
    """Top coarse L1 episode as explicit candidate context for downstream evidence collection."""
    episodes = (verdict or {}).get("episodes") or []
    if not episodes:
        return None
    top = episodes[0]
    return {"start": top["start"], "end": top["end"], "pwr_mass": top["pwr_mass"],
            "status": "candidate"}


def confirmation_is_fresh(confirmation, current_horizon):
    """Whether an L1 confirmation result matches the current corpus and threshold contract."""
    if not confirmation or not current_horizon:
        return False
    if (confirmation.get("thresholds") or {}) != config.confirmation_thresholds():
        return False
    saved = confirmation.get("corpus_horizon") or {}
    return (saved.get("snapshot_date"), saved.get("snapshot_revid")) == tuple(current_horizon)


def _source_adequacy(source_state, current_horizon, now=None):
    """Normalize explicit source metadata into a status and adequacy reason."""
    state = source_state or {}
    status = state.get("source_status", "unchecked")
    if status in {"partial", "unavailable"}:
        return status, state.get("reason")
    if status != "current_complete":
        return status, None

    checked_at = state.get("source_checked_at")
    if checked_at:
        try:
            checked = dt.datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            return "stale", "source check timestamp is invalid"
        current_time = now or dt.datetime.now(dt.timezone.utc)
        if current_time - checked > SOURCE_CHECK_MAX_AGE:
            return "stale", f"source check expired after {SOURCE_CHECK_MAX_AGE.days} days"

    source_revid = state.get("source_latest_revid")
    cached_revid = current_horizon[1] if current_horizon else None
    if source_revid is not None and cached_revid is not None and source_revid > cached_revid:
        return "stale", (
            f"source revision {source_revid} is ahead of cached snapshot revision {cached_revid}"
        )
    return status, None


def resolve_l1_state(verdict, confirmation, current_horizon, source_state=None, now=None):
    """Resolve cached coarse and exact L1 evidence into one authoritative state."""
    coarse_status = (verdict or {}).get("verdict")
    candidate_status = {
        "PIVOT?": "pivot_candidate",
        "CREEP?": "creep_candidate",
        "HEALTHY": "no_candidate",
        "SKIP": "unavailable",
    }.get(coarse_status, "unavailable")
    fresh = confirmation_is_fresh(confirmation, current_horizon)
    exact_status = confirmation.get("status") if fresh else None
    source_status, source_reason = _source_adequacy(source_state, current_horizon, now)
    is_source_inadequate = source_status in {"partial", "stale", "unavailable"}

    analysis_status = "unavailable" if candidate_status == "unavailable" else "available"
    if exact_status == "unavailable" or is_source_inadequate:
        analysis_status = "unavailable"

    if is_source_inadequate:
        resolved_status = "unavailable"
        confirmation_status = "unavailable"
    elif exact_status in {"confirmed", "not_confirmed", "unavailable"}:
        resolved_status = exact_status
        confirmation_status = exact_status
    elif candidate_status == "no_candidate":
        resolved_status = "healthy"
        confirmation_status = "not_applicable"
    elif candidate_status == "unavailable":
        resolved_status = "unavailable"
        confirmation_status = "unavailable"
    else:
        resolved_status = "candidate"
        confirmation_status = "not_run"

    reason = None
    if is_source_inadequate:
        reason = source_reason
    elif exact_status == "unavailable":
        reason = (confirmation or {}).get("reason")

    return {
        "command_status": "completed",
        "source_status": source_status,
        "analysis_status": analysis_status,
        "candidate_status": candidate_status,
        "confirmation_status": confirmation_status,
        "confirmation_fresh": fresh,
        "semantic_role": "research_lead",
        "resolved_status": resolved_status,
        "corpus_horizon": {
            "snapshot_date": current_horizon[0],
            "snapshot_revid": current_horizon[1],
            "source_checked_at": (source_state or {}).get("source_checked_at"),
            "source_latest_revid": (source_state or {}).get("source_latest_revid"),
            "expected_snapshots": (source_state or {}).get("expected_snapshots"),
            "loaded_snapshots": (source_state or {}).get("loaded_snapshots"),
        } if current_horizon else None,
        "reason": reason,
        "confirmed_episodes": (
            (confirmation or {}).get("confirmed_episodes") or []
        ) if exact_status == "confirmed" else [],
    }


def _confirmed_window(confirmation, current_horizon):
    """Return a confirmed framing window only when its corpus horizon is still current."""
    if not confirmation or confirmation.get("status") != "confirmed":
        return None
    if not confirmation_is_fresh(confirmation, current_horizon):
        return None
    episodes = confirmation.get("confirmed_episodes") or []
    if not episodes:
        return None
    top = episodes[0]
    return {
        "start": top["candidate_start"], "end": top["candidate_end"],
        "pwr_mass": top["pwr_mass"], "status": "confirmed",
        "before_revid": top["before_revid"], "before_timestamp": top["before_timestamp"],
        "after_revid": top["after_revid"], "after_timestamp": top["after_timestamp"],
        "durable_spine_drop": top["durable_spine_drop"],
    }


def framing_window(article):
    """Return framing context only when allowed by the authoritative L1 state."""
    if not config.DB.exists():
        return None
    con = duckdb.connect(str(config.DB), read_only=True)
    try:
        if _snap_count(con, article) < 3:
            return None
        corpus = Corpus(con)
        horizon = corpus.latest_snapshot(article)
        confirmation = drift.load_confirmation(article)
        verdict = drift.verdict_dict(con, article)
        source_state = provenance.load_source_state(con, article)
        state = resolve_l1_state(verdict, confirmation, horizon, source_state)
        if state["resolved_status"] == "confirmed":
            return _confirmed_window(confirmation, horizon)
        if state["resolved_status"] == "candidate":
            return _pivot_window(verdict)
        return None
    finally:
        con.close()


def _corroboration(result):
    """Count how many independent layers corroborate an anomaly — a lead count, not a verdict.
    Each signal is an independent instrument; agreement adds confidence without compounding errors."""
    signals = []
    l1 = result.get("l1") or ""
    l1_state = result.get("l1_state") or {}
    confirmation_status = l1_state.get("confirmation_status")
    if confirmation_status == "confirmed":
        signals.append("l1_pivot")
    elif confirmation_status not in {"not_confirmed", "unavailable", "not_applicable"} and (
        l1 and not l1.startswith(("HEALTHY", "SKIP", "n/a"))
    ):
        signals.append("l1_pivot")
    l2 = result.get("l2") or {}
    shifts = (l2.get("shifts") if isinstance(l2, dict) else None) or {}
    if isinstance(shifts, dict) and any(
        isinstance(shift, dict) and shift.get("shifted") for shift in shifts.values()
    ):
        signals.append("l2_shift")
    lex = result.get("lexical") or {}
    if (
        isinstance(lex, dict)
        and lex.get("mode") == "pivot_relative"
        and lex.get("adequate") is True
        and (lex.get("js_divergence") or 0) > 0.05
    ):
        signals.append("lexical_drift")
    m = result.get("mscore") or {}
    if isinstance(m, dict):
        refined = m.get("refined") or {}
        if isinstance(refined, dict) and refined.get("M"):
            signals.append("mscore_contested")
    fr = result.get("framing") or result.get("l5") or {}
    if isinstance(fr, dict):
        if any(d.get("verdict") == "contradict" for d in (fr.get("divergences") or [])):
            signals.append("framing_contradict")
        elif any(d.get("verdict") in ("differ", "absent_en", "absent_other")
                 for d in (fr.get("divergences") or [])):
            signals.append("framing_differ")
    return {"count": len(signals), "signals": signals}


def run(article, llm=False, corroborate=False, framing=False, provider=None, model=None, base_url=None):
    """Orchestrate the layers for one article. Returns a consolidated result dict.

    provider/model/base_url select the LLM backend for the opt-in L2 + framing layers (see llm.py).
    The cross-language lead comparison (L5) is opt-in via --framing. It uses matched historical revisions when
    L1 supplies a candidate window and falls back to a current static comparison otherwise."""
    article = provenance.resolve_article_title(article).canonical_title

    # Build the LLM client ONCE and share it across L2 + L5 (was threaded as 3 loose params into each verb).
    # NB the `llm` parameter here is the bool opt-in flag, so import the module under an alias.
    client = None
    if llm:
        from . import llm as llm_backend
        client = llm_backend.make_client(provider, model, base_url)
    print(f"=== WIKIDRIFT PIPELINE — {article} ===")

    # ---- L1 drift (offline from cache; else full analyze fetches + builds) ----
    con = duckdb.connect(str(config.DB), read_only=True)
    if _snap_count(con, article) < 3:
        con.close()
        print("(no cached snapshots — running full L1 analyze)\n")
        drift.analyze(article)
        con = duckdb.connect(str(config.DB), read_only=True)
    verdict = drift.verdict_dict(con, article) if _snap_count(con, article) >= 3 else None
    label = drift.candidate_verdict(con, article)[1] if verdict else "n/a (too few snapshots)"
    horizon = Corpus(con).latest_snapshot(article) if verdict else None
    confirmation = drift.load_confirmation(article)
    source_state = provenance.load_source_state(con, article)
    l1_state = resolve_l1_state(verdict, confirmation, horizon, source_state=source_state)
    if l1_state["confirmation_status"] == "confirmed":
        pivot_window = _confirmed_window(confirmation, horizon)
    elif l1_state["resolved_status"] == "candidate":
        pivot_window = _pivot_window(verdict)
    else:
        pivot_window = None
    if l1_state["resolved_status"] == "confirmed":
        episode_count = len(l1_state["confirmed_episodes"])
        label = f"CONFIRMED — {episode_count} exact episode(s)"
    elif l1_state["resolved_status"] == "not_confirmed":
        label = "NOT_CONFIRMED — exact analysis rejected the coarse candidate"
    elif l1_state["resolved_status"] == "unavailable":
        reason = l1_state["reason"] or "insufficient cached evidence"
        label = f"UNAVAILABLE — {reason}"
    print(f"\nL1 drift verdict: {label}")

    # ---- pre-rank router (metadata-only) ----
    leads = []
    if l1_state["analysis_status"] == "unavailable":
        print(f"router: unavailable ({l1_state['reason'] or 'L1 evidence unavailable'})")
    else:
        try:
            leads = prerank.prerank(con, article).get("leads", [])
        except Exception as e:                              # noqa: BLE001 — degrade if metadata missing
            print(f"router: unavailable ({e})")
    con.close()
    print(f"router leads: {', '.join(leads) if leads else '(no structural anomaly)'}")

    # ---- adjudicate the routed L2 leads (the gap this pipeline closes) ----
    l2_leads = [l for l in leads if l.endswith("→L2")]
    l2_done = False
    l2_summary = None
    if l2_leads:
        if llm:
            print(f"\n→ adjudicating routed lead(s) {l2_leads} with L2 stance:\n")
            try:
                l2_summary = stance.stance_over_time(article, entities=_pipeline_entities(article, None), client=client)
                l2_done = True
            except Exception as e:                          # noqa: BLE001
                print(f"  L2 skipped: {e}")
        else:
            print(f"→ routed to L2 {l2_leads}: re-run with --llm to adjudicate (needs an LLM key).")

    # ---- M-score corroborator (opt-in; no LLM) ----
    m = None
    if corroborate:
        try:
            m = mscore.run([article]).get(article)
        except Exception as e:                              # noqa: BLE001
            print(f"M-score skipped: {e}")

    # ---- L2.5 lexical drift (offline lead) ----
    lex = None
    try:
        print()
        lexical_mode = (
            "pivot_relative" if l1_state["confirmation_status"] == "confirmed"
            else "not_applicable"
        )
        lex = lexical.lexical_drift(article, mode=lexical_mode, window=pivot_window)
    except Exception as e:                                  # noqa: BLE001
        print(f"lexical drift skipped: {e}")

    # ---- L5 cross-language lead comparison (opt-in; LLM) ----
    # Runs regardless of L1 verdict: pivot articles → drift corroborator; healthy articles → static born-bias check.
    framing_result = None
    if framing:
        try:
            from . import l5_framing_lite
            framing_result = l5_framing_lite.framing_lite(
                article, pivot_window=pivot_window, client=client
            )
        except Exception as e:                              # noqa: BLE001
            print(f"Cross-language lead comparison skipped: {e}")

    # ---- consolidated lead ----
    print("\n── CONSOLIDATED LEAD (not a verdict) ──")
    print(f"  L1 drift : {label}")
    print(f"  router   : {', '.join(leads) if leads else 'no structural anomaly'}")
    if l2_leads:
        shifted_entities = [
            entity for entity, shift in ((l2_summary or {}).get("shifts") or {}).items()
            if isinstance(shift, dict) and shift.get("shifted")
        ]
        if not l2_done:
            l2_read = "PENDING (--llm) — a reframe-by-addition/churn is a semantic call"
        elif shifted_entities:
            l2_read = f"endpoint shift detected for {', '.join(shifted_entities)}"
        else:
            l2_read = "adjudicated — no endpoint shift detected"
        print(f"  L2 stance: {l2_read}")
    if m is not None:
        refined = m.get("refined", {}).get("M") if isinstance(m.get("refined"), dict) else m.get("refined")
        read = "low ⇒ not fought-over" if not refined else "contested (controversy ≠ malice)"
        print(f"  M-score  : refined M={refined} — {read}")
    if lex is not None and isinstance(lex, dict):
        print(
            f"  lexical  : {lex.get('mode', 'unknown')}"
            f"; JS divergence={lex.get('js_divergence', 'n/a')}"
        )
    if framing_result:
        n = len(framing_result.get("divergences") or [])
        mode = "static" if not framing_result.get("pivot_window") else "pivot-corroborator"
        print(f"  framing  : {n} divergence(s) [{mode}] — see findings/{article.replace(' ','_')}.framing.json")
    else:
        print("  L5 framing: run via `wikidrift framing` or `wikidrift pipeline --framing` (separate instrument)")
    result = {"article": article, "l1": label, "l1_state": l1_state,
              "leads": leads, "l2_adjudicated": l2_done,
              "l2": l2_summary,
              "mscore": m, "lexical": lex, "l5": framing_result}
    corr = _corroboration(result)
    print(f"  corroboration: {corr['count']} signal(s) — {corr['signals'] or '(none)'}")
    return result

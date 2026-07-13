"""L1 → L2 → L5 orchestration for one article, following the pre-rank router.

Closes the methodology's "adjudicate the routed leads" gap: `prerank` raises `addition→L2` / `churn→L2`
for reframe-by-addition/churn (vectors the removal metric is structurally blind to), but nothing consumed
them — L2 had to be run by hand. This pipeline chains the layers and, when the router routes to L2,
actually runs L2 on that article.

Layers by cost (so the default stays offline and keyless):
  L1 drift + pre-rank router — offline (cached corpus); always run.
    L2.5 lexical drift         — offline term-distribution lead; always run.
  M-score corroborator       — Action API, no LLM; opt-in (`--mscore`).
  L2 stance + L5 external ref — LLM (needs an LLM key; default Anthropic, --provider/--model/--base-url for a
                                cheaper/local backend); opt-in (`--llm`). L5 persists to the viewer.

Every output is a LEAD for a researcher, never a published verdict.
"""
import duckdb

from . import config, drift, prerank, stance, l5_crosslingual, l5_factcheck, lexical, mscore
from .corpus import Corpus


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


def run(article, llm=False, corroborate=False, provider=None, model=None, base_url=None):
    """Orchestrate the layers for one article. Returns a consolidated result dict.

    provider/model/base_url select the LLM backend for the opt-in L2/L5 layers (see llm.py)."""
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
    label = drift.candidate_verdict(con, article)[1] if _snap_count(con, article) >= 3 else "n/a (too few snapshots)"
    print(f"\nL1 drift verdict: {label}")

    # ---- pre-rank router (metadata-only) ----
    leads = []
    try:
        leads = prerank.prerank(con, article).get("leads", [])
    except Exception as e:                                  # noqa: BLE001 — degrade if metadata missing
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
        lex = lexical.lexical_drift(article)
    except Exception as e:                                  # noqa: BLE001
        print(f"lexical drift skipped: {e}")

    # ---- L5 external reference (opt-in; LLM; persists to the viewer) ----
    l5 = {}
    if llm:
        l5_context = {
            "router_leads": l2_leads,
            "entities": _pipeline_entities(article, l2_summary),
            "l2_shifts": (l2_summary or {}).get("shifts") or {},
            "lexical": {"js_divergence": lex.get("js_divergence")} if isinstance(lex, dict) else {},
        }
        for name, fn in (("crosslingual", l5_crosslingual.crosslingual), ("factcheck", l5_factcheck.factcheck)):
            try:
                print()
                l5[name] = fn(article, client=client, context=l5_context)
            except Exception as e:                          # noqa: BLE001
                print(f"L5 {name} skipped: {e}")
    else:
        print("L5 (cross-lingual framing + fact divergence): re-run with --llm — persists to the viewer.")

    # ---- consolidated lead ----
    print("\n── CONSOLIDATED LEAD (not a verdict) ──")
    print(f"  L1 drift : {label}")
    print(f"  router   : {', '.join(leads) if leads else 'no structural anomaly'}")
    if l2_leads:
        print(f"  L2 stance: {'adjudicated above' if l2_done else 'PENDING (--llm) — a reframe-by-addition/churn is a semantic call'}")
    if m is not None:
        refined = m.get("refined", {}).get("M") if isinstance(m.get("refined"), dict) else m.get("refined")
        read = "low ⇒ not fought-over ⇒ route-to-L5" if not refined else "contested (controversy ≠ malice)"
        print(f"  M-score  : refined M={refined} — {read}")
    if lex is not None and isinstance(lex, dict):
        print(f"  lexical  : JS divergence={lex.get('js_divergence', 'n/a')}")
    if l5:
        print(f"  L5       : {', '.join(l5)} computed (see the viewer)")
    elif not llm:
        print("  L5       : not run (offline) — --llm for the external-reference read")
    return {"article": article, "l1": label, "leads": leads, "l2_adjudicated": l2_done,
            "mscore": m, "lexical": lex, "l5": l5}

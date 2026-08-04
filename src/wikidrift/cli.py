"""wikidrift command-line entry point.

  wikidrift analyze "Zionism"          # full L1 pipeline (fetches as needed) + attribution
    wikidrift backfill-attribution "Zionism"  # add attribution to current exact confirmations
    wikidrift backfill-interval-profile "Zionism"  # add interval receipts from the local corpus
  wikidrift validate ["Zionism" ...]   # offline PWR candidate verdicts (no WikiWho); default = whole cache
  wikidrift prerank ["Zionism" ...]    # metadata pre-ranker (offline)
  wikidrift benchmark [--json]         # score the adjudicated roster
    wikidrift calibrate-concentration ARTICLES_DIR [--json]  # raw exact-event feature report (offline)
    wikidrift confirmed-graph ARTICLES_DIR [--json]  # fresh exact cross-article graph (offline)
  wikidrift stance "Nakba" [--entities a,b,c] [--max-snaps N]   # L2 stance classifier (needs an LLM key)
    wikidrift crosslingual "Zionism" [--langs en,he,ar] [--no-pivot]  # cross-language stance (needs key)
  wikidrift factcheck "Warsaw concentration camp" [--langs ..] [--asof 2018-06-01]  # L5 #2 fact divergence (needs key)
  wikidrift mscore ["Zionism" ...]      # Yasseri mutual-revert controversy corroborator (offline fetch)
  wikidrift ingest "Naliboki massacre" [...] [--force]   # local wikiwho_rs-on-dumps rsnap ingestion
  wikidrift pipeline "Nakba" [--llm] [--mscore]          # L1→router→(L2/L5) orchestration for one article
    wikidrift lexical "Zionism" [--top-n 20] [--min-total 3]  # L2.5 lexical drift lead (offline)

LLM verbs (stance/crosslingual/factcheck/pipeline) accept --provider {anthropic|openai|xai|grok|google} --model NAME
--base-url URL to pick a cheaper/local backend (default Anthropic). --base-url + --provider openai reaches any
OpenAI-compatible endpoint (OpenRouter/Together/Groq/DeepSeek; local Ollama/LM Studio/vLLM). Keys via the
provider's env var (ANTHROPIC_API_KEY/OPENAI_API_KEY/GOOGLE_API_KEY) or WIKIDRIFT_LLM_API_KEY; env equivalents
WIKIDRIFT_LLM_PROVIDER/_MODEL/_BASE_URL.
"""
import json
import sys
import argparse
import pathlib
from urllib.parse import urlparse, parse_qs, unquote

import duckdb

from . import (config, drift, prerank, benchmark, stance, l5_crosslingual, l5_factcheck,  # noqa: F401
               framing_trajectory, provenance,
               mscore, ingest, pipeline, l4, l5_sources, lexical, bootstrap, shards)
from .corpus import Corpus


def _normalize_article_arg(value):
    """Accept either an article title or a Wikipedia URL and return a normalized title.

    Supported URL shapes:
    - https://<lang>.wikipedia.org/wiki/<Title>
    - https://<lang>.wikipedia.org/w/index.php?title=<Title>
    """
    raw = (value or "").strip()
    if not raw:
        return raw
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return raw

    parsed = urlparse(raw)
    if "wikipedia.org" not in (parsed.netloc or ""):
        # Non-Wikipedia URL: leave unchanged so downstream errors remain explicit.
        return raw

    title = ""
    if "/wiki/" in parsed.path:
        title = parsed.path.split("/wiki/", 1)[1]
    elif parsed.path.endswith("/w/index.php"):
        title = parse_qs(parsed.query).get("title", [""])[0]

    title = unquote(title).replace("_", " ").strip()
    return title or raw


def _migrate_shards(args):
    """Copy and verify canonical data into independently writable article shards."""
    source_dir = args.source_data_dir or config.DATA_DIR
    report = shards.migrate_all(source_dir, args.articles_dir)
    print(f"migrated {report['article_count']} article shard(s) into "
          f"{args.articles_dir or source_dir / 'articles'}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="wikidrift", description="editor-agnostic Wikipedia narrative-drift detector")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_llm_flags(sp):
        """Provider-selection flags shared by the LLM verbs (arg → env → default; see llm.py)."""
        sp.add_argument("--provider", default=None, choices=["anthropic", "openai", "xai", "grok", "google"],
                        help="LLM provider (default: auto priority from WIKIDRIFT_LLM_PROVIDER_PRIORITY;"
                            " explicit provider or WIKIDRIFT_LLM_PROVIDER disables failover)")
        sp.add_argument("--model", default=None, help="model id (default: provider default / WIKIDRIFT_LLM_MODEL)")
        sp.add_argument("--base-url", dest="base_url", default=None,
                        help="OpenAI-compatible endpoint base URL (openai provider): OpenRouter/Groq/Ollama/…")

    sp = sub.add_parser("analyze", help="full L1 pipeline for one article (+ attribution)")
    sp.add_argument("article")

    sp = sub.add_parser(
        "backfill-attribution",
        help="add exact-event attribution to a current confirmation without rerunning L1",
    )
    sp.add_argument("article")
    sp.add_argument("--force", action="store_true", help="recompute episodes that already have attribution")

    sp = sub.add_parser(
        "backfill-interval-profile",
        help="add interval-level PWR receipts to a current confirmation without rerunning L1",
    )
    sp.add_argument("article")
    sp.add_argument("--force", action="store_true", help="recompute an interval profile that already exists")

    sp = sub.add_parser(
        "backfill-process-context",
        help="attach neutral editorial-process receipts to current exact confirmations",
    )
    sp.add_argument("article")
    sp.add_argument("--force", action="store_true", help="refetch episodes that already have process context")

    sp = sub.add_parser("validate", help="offline PWR candidate verdicts (no WikiWho)")
    sp.add_argument("articles", nargs="*")

    sp = sub.add_parser("prerank", help="metadata-only candidate pre-ranker")
    sp.add_argument("articles", nargs="*")

    sp = sub.add_parser("benchmark", help="score the adjudicated ground-truth roster")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser(
        "calibrate-concentration",
        help="report raw concentration features from fresh article-owned confirmations",
    )
    sp.add_argument("articles_dir", type=pathlib.Path)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("stance", help="L2 LLM stance classifier over time")
    sp.add_argument("article")
    sp.add_argument("--entities", default=None,
                    help="comma-separated entities (default: article title, controversy-agnostic)")
    sp.add_argument("--max-snaps", type=int, default=0)
    sp.add_argument("--since", default=None, help="ISO date; only snapshots on/after it (target the L1 pivot window)")
    sp.add_argument("--repeated-runs", type=int, default=stance.DEFAULT_REPEATED_RUNS,
                    help="total runs for passages around an apparent transition")
    add_llm_flags(sp)

    sp = sub.add_parser("crosslingual", help="L5 cross-language stance comparison")
    sp.add_argument("article")
    sp.add_argument("--langs", default=None, help="comma-separated editions (default: auto established set)")
    sp.add_argument("--no-pivot", action="store_true", help="static divergence only (skip pivot-relative)")
    add_llm_flags(sp)

    sp = sub.add_parser("factcheck", help="L5 #2 — cross-edition citation + claim divergence")
    sp.add_argument("article")
    sp.add_argument("--langs", default=None, help="comma-separated editions (default: all)")
    sp.add_argument("--max-langs", type=int, default=0,
                    help="cap editions for stability (0 = no cap)")
    sp.add_argument("--asof", default=None, help="ISO date, e.g. 2018-06-01 (compare editions as of then)")
    add_llm_flags(sp)

    sp = sub.add_parser("mscore", help="Yasseri mutual-revert controversy corroborator")
    sp.add_argument("articles", nargs="*")
    sp.add_argument("--force", action="store_true", help="re-fetch revision history, bypassing the cache")

    sp = sub.add_parser("ingest", help="local wikiwho_rs-on-dumps rsnap ingestion (coverage gaps / batch)")
    sp.add_argument("articles", nargs="+")
    sp.add_argument("--force", action="store_true", help="re-ingest via local even if snapshots exist")

    sp = sub.add_parser("audit-snapshots", help="offline semantic-integrity audit of loaded snapshots")
    sp.add_argument("articles", nargs="*", help="articles to audit (default: all loaded articles)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--write", action="store_true",
                    help="persist receipts and quarantine source state (default: report only)")

    sp = sub.add_parser("audit-endpoints", help="offline stable-current-endpoint audit")
    sp.add_argument("articles", nargs="*", help="articles to audit (default: all loaded articles)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--write", action="store_true",
                    help="persist endpoint receipts (default: report only)")

    sp = sub.add_parser("pipeline", help="L1→router→(L2/L5) orchestration for one article")
    sp.add_argument("article")
    sp.add_argument("--llm", action="store_true", help="run L2 stance on routed leads (needs an LLM key)")
    sp.add_argument("--mscore", action="store_true", help="also run the M-score controversy corroborator")
    sp.add_argument("--framing", action="store_true",
                    help="run L5 cross-language lead comparison (prefers fresh confirmed L1 pair; needs an LLM key)")
    sp.add_argument("--facts", action="store_true",
                    help="run L5 cross-edition fact checks after every confirmed episode (needs an LLM key)")
    sp.add_argument("--max-langs", type=int, default=0,
                    help="cap editions used by each fact check (0 = no cap)")
    sp.add_argument("--additive", action="store_true",
                    help="trace persistent additions across exact stable revisions")
    sp.add_argument("--process-context", action="store_true",
                    help="fetch neutral editorial-process receipts for fresh confirmed events")
    add_llm_flags(sp)

    sp = sub.add_parser("framing-trajectory", help="deterministic addition-side framing trajectory")
    sp.add_argument("article")
    sp.add_argument("--mode", choices=["formative", "interval", "exact_event"], default="formative")
    sp.add_argument("--start", default=None, help="interval start date (inclusive)")
    sp.add_argument("--end", default=None, help="interval end date (inclusive)")
    sp.add_argument("--revision-ids", default=None,
                    help="ordered comma-separated revision IDs for exact_event mode")

    sp = sub.add_parser("framing", help="L5 cross-language lead comparison around confirmed L1 pair or candidate")
    sp.add_argument("article")
    sp.add_argument("--static", action="store_true",
                    help="compare current leads only, ignoring any cached L1 candidate window")
    sp.add_argument("--recategorize", action="store_true", help="force re-run of the LLM category classification")
    add_llm_flags(sp)

    sp = sub.add_parser("confirmed-graph", help="L4 offline graph from fresh exact attribution in article shards")
    sp.add_argument("articles_dir", type=pathlib.Path)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("discover", help="L4 graph-guided discovery: exact seed → footprint → exact L1 retest")
    sp.add_argument("article", nargs="?", default="Zionism", help="seed article (default: Zionism)")
    sp.add_argument("--top-n", type=int, default=l4.SEED_TOP_N,
                    help="seed from the top-N editors attributed with established-token removals")
    sp.add_argument("--limit", type=int, default=l4.CANDIDATE_LIMIT, help="max fresh candidates to L1 re-test")

    sp = sub.add_parser("sources", help="L5 #3b — citation-source composition over time (reference-agnostic)")
    sp.add_argument("article")
    sp.add_argument("--max-snaps", type=int, default=12, help="snapshots to sample (bounds Action-API fetches)")

    sp = sub.add_parser("lexical", help="L2.5 lexical drift lead (term keyness + JS divergence)")
    sp.add_argument("article")
    sp.add_argument("--top-n", type=int, default=20, help="terms to keep per side (gained/lost)")
    sp.add_argument("--min-total", type=int, default=3, help="minimum total count across windows per term")

    sp = sub.add_parser("profile", help="descriptive L1 drift profile (recency + editor concentration, offline)")
    sp.add_argument("article")

    sp = sub.add_parser("bootstrap", help="populate the token corpus for a slate (default: benchmark roster)")
    sp.add_argument("articles", nargs="*", help="articles to fetch (default: the adjudicated roster)")

    sp = sub.add_parser("migrate-shards", help="copy and verify canonical data into per-article shards")
    sp.add_argument("--source-data-dir", type=pathlib.Path, default=None,
                    help="canonical data directory (default: WIKIDRIFT_DATA_DIR or historical location)")
    sp.add_argument("--articles-dir", type=pathlib.Path, default=None,
                    help="destination directory (default: <source>/articles)")
    sp.set_defaults(handler=_migrate_shards)

    args = p.parse_args(argv)

    if hasattr(args, "handler"):
        args.handler(args)
        return

    if args.cmd == "analyze":
        drift.analyze(_normalize_article_arg(args.article))
    elif args.cmd == "backfill-attribution":
        article = _normalize_article_arg(args.article)
        report = drift.backfill_attribution(article, force=args.force)
        print(
            f"{article}: {report['updated_episodes']} updated, "
            f"{report['skipped_episodes']} unchanged, {report['failed_episodes']} failed"
        )
        if report["failed_episodes"]:
            raise RuntimeError(f"attribution failed for {report['failed_episodes']} episode(s)")
    elif args.cmd == "backfill-interval-profile":
        article = _normalize_article_arg(args.article)
        report = drift.backfill_interval_profile(article, force=args.force)
        action = "unchanged" if report["skipped"] else "updated"
        print(f"{article}: {action}, {report['interval_count']} interval(s)")
    elif args.cmd == "backfill-process-context":
        article = _normalize_article_arg(args.article)
        report = drift.backfill_process_context(article, force=args.force)
        print(
            f"{article}: {report['updated_episodes']} updated, "
            f"{report['skipped_episodes']} unchanged, {report['failed_episodes']} failed"
        )
        if report["failed_episodes"]:
            raise RuntimeError(f"process context failed for {report['failed_episodes']} episode(s)")
    elif args.cmd == "validate":
        con = duckdb.connect(str(config.DB), read_only=True)
        targets = args.articles or Corpus(con).articles_with_snapshots(3)
        results = [drift.candidate_verdict(con, a) for a in targets]
        con.close()
        print("\n" + "=" * 72)
        print("CANDIDATE VERDICTS (PWR-grounded coarse metric, ranked by PWR-mass; recency = context, unconfirmed):")
        print("=" * 72)
        for article, label in results:
            print(f"  {article:<30} {label}")
    elif args.cmd == "prerank":
        prerank.run(args.articles or None)
    elif args.cmd == "benchmark":
        benchmark.run(as_json=args.json)
    elif args.cmd == "calibrate-concentration":
        benchmark.run_concentration(args.articles_dir, as_json=args.json)
    elif args.cmd == "stance":
        ents = [e.strip() for e in args.entities.split(",")] if args.entities else None
        stance.stance_over_time(_normalize_article_arg(args.article), entities=ents, max_snaps=args.max_snaps,
                                since=args.since,
                                repeated_runs=args.repeated_runs,
                                provider=args.provider, model=args.model, base_url=args.base_url)
    elif args.cmd == "crosslingual":
        langs = [l.strip() for l in args.langs.split(",")] if args.langs else None
        l5_crosslingual.crosslingual(_normalize_article_arg(args.article), langs=langs, pivot=not args.no_pivot,
                                     provider=args.provider, model=args.model, base_url=args.base_url)
    elif args.cmd == "factcheck":
        langs = [l.strip() for l in args.langs.split(",")] if args.langs else None
        ts = f"{args.asof}T00:00:00Z" if args.asof else None
        article = _normalize_article_arg(args.article)
        windows = pipeline.framing_windows(article) if not ts else []
        confirmed_windows = [window for window in windows if window.get("status") == "confirmed"]
        if confirmed_windows:
            from . import llm as llm_backend
            client = llm_backend.make_client(args.provider, args.model, args.base_url)
            pipeline.analyze_confirmed_episodes(
                article, confirmed_windows, client=client,
                lexical_layer=False, sources=False, facts=True,
                factcheck_options={"langs": langs, "max_langs": (args.max_langs or None)},
            )
        else:
            l5_factcheck.factcheck(article, langs=langs, ts=ts,
                                   provider=args.provider, model=args.model, base_url=args.base_url,
                                   max_langs=(args.max_langs or None))
    elif args.cmd == "mscore":
        mscore.run(args.articles or ["Zionism", "Nakba", "Warsaw concentration camp",
                                     "Photosynthesis", "Climate change"], force=args.force)
    elif args.cmd == "ingest":
        ingest.ingest_articles(args.articles, force=args.force)
    elif args.cmd == "audit-snapshots":
        con = duckdb.connect(str(config.DB), read_only=not args.write)
        try:
            report = provenance.audit_snapshot_integrity(
                con, args.articles or None, persist=args.write
            )
        finally:
            con.close()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            totals = report["totals"]
            print(
                f"snapshot integrity: {report['article_count']} article(s), "
                f"{totals['complete']} complete, {totals['suspect']} suspect, "
                f"{totals['quarantined']} quarantined"
            )
            for article in report["articles"]:
                if article["status"] != "complete":
                    print(f"  {article['status'].upper():<11} {article['article']}: {article['counts']}")
    elif args.cmd == "audit-endpoints":
        con = duckdb.connect(str(config.DB), read_only=not args.write)
        try:
            report = provenance.audit_stable_endpoints(
                con, args.articles or None, persist=args.write
            )
        finally:
            con.close()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            totals = report["totals"]
            print(
                f"stable endpoints: {report['article_count']} article(s), "
                f"{totals['stable']} stable, {totals['unstable']} unstable"
            )
            for article in report["articles"]:
                if article["status"] == "unstable":
                    print(f"  UNSTABLE    {article['article']}")
    elif args.cmd == "pipeline":
        pipeline.run(_normalize_article_arg(args.article), llm=args.llm, corroborate=args.mscore,
                     framing=args.framing, facts=args.facts, additive=args.additive,
                     process=args.process_context,
                     provider=args.provider, model=args.model, base_url=args.base_url,
                     factcheck_max_langs=(args.max_langs or None))
    elif args.cmd == "framing-trajectory":
        revision_ids = [
            int(revision_id.strip())
            for revision_id in (args.revision_ids or "").split(",")
            if revision_id.strip()
        ]
        result = framing_trajectory.analyze_article(
            _normalize_article_arg(args.article),
            mode=args.mode,
            start=args.start,
            end=args.end,
            revision_ids=revision_ids,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.cmd == "framing":
        from . import l5_framing_lite
        article = _normalize_article_arg(args.article)
        windows = [] if args.static else pipeline.framing_windows(article)
        confirmed_windows = [window for window in windows if window.get("status") == "confirmed"]
        if confirmed_windows:
            from . import llm as llm_backend
            client = llm_backend.make_client(args.provider, args.model, args.base_url)
            pipeline.analyze_confirmed_episodes(
                article, confirmed_windows, client=client, framing=True,
                lexical_layer=False, sources=False,
                framing_options={"recategorize": args.recategorize},
            )
        else:
            pivot_window = windows[0] if windows else None
            l5_framing_lite.framing_lite(article, pivot_window=pivot_window,
                                         recategorize=args.recategorize,
                                         provider=args.provider, model=args.model, base_url=args.base_url)
    elif args.cmd == "confirmed-graph":
        l4.run_confirmed_graph(args.articles_dir, as_json=args.json)
    elif args.cmd == "discover":
        l4.discover(_normalize_article_arg(args.article), top_n=args.top_n, limit=args.limit)
    elif args.cmd == "sources":
        article = _normalize_article_arg(args.article)
        windows = pipeline.framing_windows(article)
        confirmed_windows = [window for window in windows if window.get("status") == "confirmed"]
        if confirmed_windows:
            pipeline.analyze_confirmed_episodes(
                article, confirmed_windows, lexical_layer=False,
                source_options={"max_snaps": args.max_snaps},
            )
        else:
            l5_sources.sources_over_time(article, max_snaps=args.max_snaps)
    elif args.cmd == "lexical":
        article = _normalize_article_arg(args.article)
        windows = pipeline.framing_windows(article)
        confirmed_windows = [window for window in windows if window.get("status") == "confirmed"]
        if confirmed_windows:
            pipeline.analyze_confirmed_episodes(
                article, confirmed_windows, sources=False,
                lexical_options={"top_n": args.top_n, "min_total": args.min_total},
            )
        else:
            lexical.lexical_drift(article, top_n=args.top_n, min_total=args.min_total)
    elif args.cmd == "profile":
        drift.profile_report(_normalize_article_arg(args.article))
    elif args.cmd == "bootstrap":
        bootstrap.run(args.articles or None)


if __name__ == "__main__":
    main()

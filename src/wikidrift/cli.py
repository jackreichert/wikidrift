"""wikidrift command-line entry point.

  wikidrift analyze "Zionism"          # full L1 pipeline (fetches as needed) + attribution
  wikidrift analyze "https://en.wikipedia.org/wiki/Zionism"  # same, accepts Wikipedia URLs
  wikidrift validate ["Zionism" ...]   # offline PWR candidate verdicts (no WikiWho); default = whole cache
  wikidrift prerank ["Zionism" ...]    # metadata pre-ranker (offline)
  wikidrift benchmark [--json]         # score the adjudicated roster
  wikidrift stance "Nakba" [--entities a,b,c] [--max-snaps N]   # L2 stance classifier (needs an LLM key)
  wikidrift crosslingual "Zionism" [--langs en,he,ar] [--no-pivot]  # L5 #1 framing divergence (needs key)
  wikidrift factcheck "Warsaw concentration camp" [--langs ..] [--asof 2018-06-01]  # L5 #2 fact divergence (needs key)
  wikidrift mscore ["Zionism" ...]      # Yasseri mutual-revert controversy corroborator (offline fetch)
  wikidrift ingest "Naliboki massacre" [...] [--force]   # local wikiwho_rs-on-dumps rsnap ingestion
  wikidrift pipeline "Nakba" [--llm] [--mscore]          # L1→router→(L2/L5) orchestration for one article

LLM verbs (stance/crosslingual/factcheck/pipeline) accept --provider {anthropic|openai|google|xai|grok}
--model NAME --base-url URL to pick a cheaper/local backend (default Anthropic). --provider xai (alias: grok)
uses Grok via https://api.x.ai/v1 (XAI_API_KEY). --base-url + --provider openai reaches any OpenAI-compatible
endpoint (OpenRouter/Together/Groq/DeepSeek; local Ollama/LM Studio/vLLM). Keys via the provider's env var
(ANTHROPIC_API_KEY/OPENAI_API_KEY/GOOGLE_API_KEY/XAI_API_KEY) or WIKIDRIFT_LLM_API_KEY; env equivalents
WIKIDRIFT_LLM_PROVIDER/_MODEL/_BASE_URL.
"""
import sys
import argparse
from urllib.parse import urlparse, unquote

import duckdb

from . import (config, drift, prerank, benchmark, stance, l5_crosslingual, l5_factcheck,  # noqa: F401
               mscore, ingest, pipeline, l4, l5_sources, bootstrap)
from .corpus import Corpus


def extract_article_title(input_str):
    """Parse Wikipedia URL or return the article title as-is.
    
    Handles:
      - https://en.wikipedia.org/wiki/Zionism → Zionism
      - https://en.wikipedia.org/wiki/Israeli–Palestinian_conflict → Israeli–Palestinian_conflict
      - Zionism → Zionism
    """
    if not input_str:
        return input_str
    
    # Check if it looks like a URL
    if input_str.startswith(("http://", "https://")):
        try:
            parsed = urlparse(input_str)
            # Extract the path component, which should be /wiki/{article_title}
            if parsed.path.startswith("/wiki/"):
                title = parsed.path[6:]  # Remove "/wiki/" prefix
                # URL-decode the title (handle %20 → space, etc.)
                title = unquote(title)
                return title
        except Exception:
            pass
    
    # Not a URL or parsing failed; return as-is
    return input_str


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="wikidrift", description="editor-agnostic Wikipedia narrative-drift detector")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_llm_flags(sp):
        """Provider-selection flags shared by the LLM verbs (arg → env → default; see llm.py)."""
        sp.add_argument("--provider", default=None,
                        choices=["anthropic", "openai", "google", "xai", "grok"],
                        help="LLM provider (default: anthropic, or WIKIDRIFT_LLM_PROVIDER; grok → xai)")
        sp.add_argument("--model", default=None, help="model id (default: provider default / WIKIDRIFT_LLM_MODEL)")
        sp.add_argument("--base-url", dest="base_url", default=None,
                        help="OpenAI-compatible endpoint base URL (openai/xai): OpenRouter/xAI/Ollama/…")

    sp = sub.add_parser("analyze", help="full L1 pipeline for one article (+ attribution)")
    sp.add_argument("article")

    sp = sub.add_parser("validate", help="offline PWR candidate verdicts (no WikiWho)")
    sp.add_argument("articles", nargs="*")

    sp = sub.add_parser("prerank", help="metadata-only candidate pre-ranker")
    sp.add_argument("articles", nargs="*")

    sp = sub.add_parser("benchmark", help="score the adjudicated ground-truth roster")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("stance", help="L2 LLM stance classifier over time")
    sp.add_argument("article")
    sp.add_argument("--entities", default=None, help="comma-separated focal entities")
    sp.add_argument("--max-snaps", type=int, default=0)
    sp.add_argument("--since", default=None, help="ISO date; only snapshots on/after it (target the L1 pivot window)")
    add_llm_flags(sp)

    sp = sub.add_parser("crosslingual", help="L5 #1 — cross-lingual framing divergence")
    sp.add_argument("article")
    sp.add_argument("--langs", default=None, help="comma-separated editions (default: topic slate)")
    sp.add_argument("--no-pivot", action="store_true", help="static divergence only (skip pivot-relative)")
    add_llm_flags(sp)

    sp = sub.add_parser("factcheck", help="L5 #2 — cross-edition citation + claim divergence")
    sp.add_argument("article")
    sp.add_argument("--langs", default=None, help="comma-separated editions (default: all)")
    sp.add_argument("--asof", default=None, help="ISO date, e.g. 2018-06-01 (compare editions as of then)")
    add_llm_flags(sp)

    sp = sub.add_parser("mscore", help="Yasseri mutual-revert controversy corroborator")
    sp.add_argument("articles", nargs="*")
    sp.add_argument("--force", action="store_true", help="re-fetch revision history, bypassing the cache")

    sp = sub.add_parser("ingest", help="local wikiwho_rs-on-dumps rsnap ingestion (coverage gaps / batch)")
    sp.add_argument("articles", nargs="+")
    sp.add_argument("--force", action="store_true", help="re-ingest via local even if snapshots exist")

    sp = sub.add_parser("pipeline", help="L1→router→(L2/L5) orchestration for one article")
    sp.add_argument("article")
    sp.add_argument("--llm", action="store_true", help="run L2 stance on routed leads + L5 (needs an LLM key)")
    sp.add_argument("--mscore", action="store_true", help="also run the M-score controversy corroborator")
    add_llm_flags(sp)

    sp = sub.add_parser("discover", help="L4 graph-guided discovery: seed → destructive footprint → L1 re-test")
    sp.add_argument("article", nargs="?", default="Zionism", help="seed article (default: Zionism)")
    sp.add_argument("--top-n", type=int, default=l4.SEED_TOP_N, help="seed from the top-N destroyers")
    sp.add_argument("--limit", type=int, default=l4.CANDIDATE_LIMIT, help="max fresh candidates to L1 re-test")

    sp = sub.add_parser("sources", help="L5 #3b — citation-source composition over time (reference-agnostic)")
    sp.add_argument("article")
    sp.add_argument("--max-snaps", type=int, default=12, help="snapshots to sample (bounds Action-API fetches)")

    sp = sub.add_parser("profile", help="descriptive L1 drift profile (recency + editor concentration, offline)")
    sp.add_argument("article")

    sp = sub.add_parser("bootstrap", help="populate the token corpus for a slate (default: benchmark roster)")
    sp.add_argument("articles", nargs="*", help="articles to fetch (default: the adjudicated roster)")

    args = p.parse_args(argv)

    if args.cmd == "analyze":
        article = extract_article_title(args.article)
        drift.analyze(article)
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
    elif args.cmd == "stance":
        article = extract_article_title(args.article)
        ents = [e.strip() for e in args.entities.split(",")] if args.entities else None
        stance.stance_over_time(article, entities=ents, max_snaps=args.max_snaps, since=args.since,
                                provider=args.provider, model=args.model, base_url=args.base_url)
    elif args.cmd == "crosslingual":
        article = extract_article_title(args.article)
        langs = [l.strip() for l in args.langs.split(",")] if args.langs else None
        l5_crosslingual.crosslingual(article, langs=langs, pivot=not args.no_pivot,
                                     provider=args.provider, model=args.model, base_url=args.base_url)
    elif args.cmd == "factcheck":
        article = extract_article_title(args.article)
        langs = [l.strip() for l in args.langs.split(",")] if args.langs else None
        ts = f"{args.asof}T00:00:00Z" if args.asof else None
        l5_factcheck.factcheck(article, langs=langs, ts=ts,
                               provider=args.provider, model=args.model, base_url=args.base_url)
    elif args.cmd == "mscore":
        mscore.run(args.articles or ["Zionism", "Nakba", "Warsaw concentration camp",
                                     "Photosynthesis", "Climate change"], force=args.force)
    elif args.cmd == "ingest":
        ingest.ingest_articles(args.articles, force=args.force)
    elif args.cmd == "pipeline":
        article = extract_article_title(args.article)
        pipeline.run(article, llm=args.llm, corroborate=args.mscore,
                     provider=args.provider, model=args.model, base_url=args.base_url)
    elif args.cmd == "discover":
        article = extract_article_title(args.article)
        l4.discover(article, top_n=args.top_n, limit=args.limit)
    elif args.cmd == "sources":
        article = extract_article_title(args.article)
        l5_sources.sources_over_time(article, max_snaps=args.max_snaps)
    elif args.cmd == "profile":
        article = extract_article_title(args.article)
        drift.profile_report(article)
    elif args.cmd == "bootstrap":
        bootstrap.run(args.articles or None)


if __name__ == "__main__":
    main()

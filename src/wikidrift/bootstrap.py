"""Corpus bootstrap — populate the token corpus (`provenance.duckdb`) for a slate of articles.

The offline verbs (`validate`, `benchmark`, `discover`, `sources`, `profile`) read the cached DuckDB corpus,
which is gitignored (binary, regenerable). This rebuilds it from public data: for each article, fetch the
revision timeline + persistent-revision snapshots (hosted WikiWho + MediaWiki Action API) into `rsnap`, then
print its offline candidate verdict so you can see it worked.

Sequential by design — the DuckDB corpus has a single writer. Supersedes spike 007's base-rate batch runner.
Hosted-coverage gaps (quieter articles, e.g. the Poland-WWII slate) return <3 snapshots and need
`wikidrift ingest "<article>"` (the local `wikiwho_rs` backend) instead.
"""
import duckdb

from . import config, provenance, drift
from .corpus import Corpus
from .benchmark import ROSTER


def run(articles=None):
    """Populate the corpus for `articles` (default: the benchmark roster). Best-effort, hosted WikiWho."""
    articles = articles or [c["article"] for c in ROSTER]
    con = duckdb.connect(str(config.DB))
    print(f"bootstrapping {len(articles)} article(s) into {config.DB.name} — sequential, hosted WikiWho\n")
    ok, gaps = 0, []
    try:
        for i, a in enumerate(articles, 1):
            print(f"[{i}/{len(articles)}] {a}", flush=True)
            try:
                provenance.ensure_sizes(con, a)
                provenance.ensure_indexes(con)
                provenance.build_snapshots(con, a)
            except Exception as ex:
                print(f"    !! error: {str(ex)[:140]}")
                continue
            n = Corpus(con).snapshot_count(a)
            if n < 3:
                gaps.append(a)
                print(f"    {n} snapshots — hosted coverage gap; try: wikidrift ingest \"{a}\"")
                continue
            _, label = drift.candidate_verdict(con, a)
            print(f"    {n} snapshots — {label}")
            ok += 1
    finally:
        con.close()
    print(f"\ndone: {ok}/{len(articles)} populated. "
          f"Offline verbs (validate/benchmark/discover/sources/profile) now work on these.")
    if gaps:
        print(f"coverage gaps (use `wikidrift ingest`): {', '.join(gaps)}")
    return ok

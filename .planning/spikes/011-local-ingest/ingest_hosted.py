"""Ingest the benchmark must-flag articles via the VALIDATED hosted-WikiWho path.

Fallback from local wikiwho_rs, which is broken on real wikitext (any <tag>/<!--comment--> drops all
preceding content — see WIKIWHO_RS_BUG.md). Hosted WikiWho covers all 8 must-flag articles correctly.

Reuses the promoted package's provenance layer (build_snapshots = persistent snapshots via hosted
tokens_at) — the same engine that produced the whole cached corpus, so results are directly comparable.

Usage: uv run python ingest_hosted.py            # all 8 must-flag articles
       uv run python ingest_hosted.py "Hamas"    # specific
"""
import sys
import duckdb
from wikidrift import config, provenance

MUST_FLAG = [
    "Hamas",
    "Jedwabne pogrom",
    "Palestinian political violence",
    "Gaza war",
    "Collaboration in German-occupied Poland",
    "Rescue of Jews by Poles during the Holocaust",
    "Naliboki massacre",
    "Warsaw concentration camp",
]


def ingest(con, article):
    print(f"=== INGEST (hosted): {article} ===", flush=True)
    have = con.execute("SELECT count(distinct snap_rev) FROM rsnap WHERE article=?", [article]).fetchone()[0]
    if have >= 3:
        print(f"  already ingested ({have} snapshots) — skipping", flush=True); return
    provenance.ensure_sizes(con, article)
    provenance.ensure_indexes(con)
    snaps = provenance.build_snapshots(con, article)
    n = con.execute("SELECT count(distinct snap_rev) FROM rsnap WHERE article=?", [article]).fetchone()[0]
    print(f"  {n} snapshots built ({len(snaps)} dates)", flush=True)


def main(articles):
    con = duckdb.connect(str(config.DB))
    for a in articles:
        try:
            ingest(con, a)
        except Exception as e:
            print(f"  !! {a}: {e}", flush=True)
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:] or MUST_FLAG)

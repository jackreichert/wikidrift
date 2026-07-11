"""Spike 001b — deleted-token lifecycle: was the long-stable text DELETED or does it SURVIVE?

Answers the question spike 002 could not: does a contested article's drift come from
REPLACEMENT of long-stable text (retrofit) or mere ADDITION around it (expansion)?

Method (the deleted-token lifecycle, §10):
  1. Reconstruct the article's token set AS OF the last pre-Oct-7-2023 revision.
  2. Diff it against today's token set by stable WikiWho `token_id`.
  3. A pre-Oct-7 token absent today = DELETED. Classify each by how long-stable it already
     was on Oct 6 2023 (origin era) → "of text stable since before 2019, how much is now gone?"

This uses the hosted WikiWho API's historical-revision endpoint for speed; `wikiwho_rs`
computes the identical structure locally from dumps for the production 10k-article batch
(validated separately — it runs locally and emits the same o_rev_id/editor/in/out).

Usage: uv run python analyze_deletion.py "Zionism" <pre_oct7_rev_id>
"""
import sys
import pathlib
import requests
import duckdb

UA = "gh-wiki-spike/0.1 (awesome@rpophesagr.com)"
WIKIWHO = "https://wikiwho.wmcloud.org/en/api/v1.0.0-beta"
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"


def fetch_snapshot(article, rev_id):
    url = f"{WIKIWHO}/rev_content/{article}/{rev_id}/?editor=true&token_id=true&o_rev_id=true&out=false&in=false"
    d = requests.get(url, headers={"User-Agent": UA}, timeout=180).json()
    rev = d["revisions"][0]
    rid = next(iter(rev))
    return rev[rid]["tokens"]


def main(article, pre_rev):
    con = duckdb.connect(str(DB))
    toks = fetch_snapshot(article, pre_rev)
    print(f"{article} as of pre-Oct-7 rev {pre_rev}: {len(toks):,} tokens")
    con.execute("CREATE OR REPLACE TABLE snap(article TEXT, token_id BIGINT, str TEXT, o_rev_id BIGINT)")
    con.executemany("INSERT INTO snap VALUES (?,?,?,?)",
                    [(article, t["token_id"], t["str"], t["o_rev_id"]) for t in toks])

    # Era = how long-stable the token already was ON Oct 6 2023 (by its origin timestamp).
    # survived = its token_id still present in today's `tokens` table for this article.
    rows = con.execute("""
        WITH s AS (
          SELECT sn.token_id,
                 CAST(rr.ts AS TIMESTAMP) AS origin_ts,
                 (cur.token_id IS NOT NULL) AS survived
          FROM snap sn
          JOIN revisions rr ON rr.article=sn.article AND rr.rev_id=sn.o_rev_id
          LEFT JOIN tokens cur ON cur.article=sn.article AND cur.token_id=sn.token_id
          WHERE sn.article=?
        )
        SELECT CASE WHEN origin_ts < TIMESTAMP '2010-01-01' THEN '1. pre-2010 (>13yr stable)'
                    WHEN origin_ts < TIMESTAMP '2015-01-01' THEN '2. 2010-2014 (9-13yr)'
                    WHEN origin_ts < TIMESTAMP '2019-01-01' THEN '3. 2015-2018 (5-8yr)'
                    WHEN origin_ts < TIMESTAMP '2022-01-01' THEN '4. 2019-2021 (2-4yr)'
                    ELSE '5. 2022-Sep2023 (<2yr)' END AS stability_era,
               count(*) AS at_oct6_2023,
               sum(CASE WHEN survived THEN 1 ELSE 0 END) AS survived_to_now,
               count(*) - sum(CASE WHEN survived THEN 1 ELSE 0 END) AS deleted,
               round(100.0*(count(*)-sum(CASE WHEN survived THEN 1 ELSE 0 END))/count(*),1) AS pct_deleted
        FROM s GROUP BY stability_era ORDER BY stability_era
    """, [article]).fetchall()

    tot = con.execute("""
        SELECT count(*), sum(CASE WHEN cur.token_id IS NOT NULL THEN 1 ELSE 0 END)
        FROM snap sn LEFT JOIN tokens cur ON cur.article=sn.article AND cur.token_id=sn.token_id
        WHERE sn.article=?""", [article]).fetchone()
    con.close()

    print(f"\nDELETED-TOKEN LIFECYCLE — {article}")
    print("How much of the pre-Oct-7 article, by how long-stable it already was, is GONE today?\n")
    print(f"{'stability as of Oct 6 2023':<30} | {'tokens':>8} | {'survived':>8} | {'deleted':>8} | {'% deleted':>9}")
    print("-"*76)
    for era, n, surv, dele, pct in rows:
        print(f"{era:<30} | {n:>8,} | {surv:>8,} | {dele:>8,} | {pct:>8}%")
    print("-"*76)
    total, survived = tot
    print(f"{'ALL pre-Oct-7 text':<30} | {total:>8,} | {survived:>8,} | {total-survived:>8,} | {round(100*(total-survived)/total,1):>8}%")
    print(f"\nInterpretation: expansion would preserve old text (low % deleted, esp. for long-stable);")
    print(f"retrofit deletes it (high % deleted even for text stable 10+ years).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Zionism",
         sys.argv[2] if len(sys.argv) > 2 else "1177123269")

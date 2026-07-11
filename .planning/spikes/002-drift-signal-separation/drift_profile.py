"""Spike 002 — does the drift signal separate a contested article from a stable control?

Reads the provenance DuckDB written by 001a and computes a per-article DRIFT PROFILE
from surviving-token provenance (§10 factors, hosted-API subset):
  - article age (first revision)
  - surviving-text age distribution (median, and % of *current* text authored
    after Oct 7 2023 — the "recent-replacement burst")
  - editor concentration on surviving text (top-10 share, distinct editors) -> authorship diversity
  - churn: % of tokens ever removed/re-added (in/out), and per-token churn intensity

The thesis test: the contested article should show a materially heavier recent-
authorship tail and/or higher editor concentration and churn *intensity* — even if
raw churn *fraction* does not separate them (a nuance we explicitly check).

Usage: uv run python drift_profile.py "Contested Title" "Control Title"
"""
import sys
import pathlib
import duckdb

DB_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"
OCT7 = "2023-10-07"

METRICS = [
    ("first_revision", "min(r.ts)"),
    ("latest_revision", "max(a.latest_time)"),
    ("surviving_tokens", "any_value(a.n_tokens)"),
    ("total_revisions", "count(distinct r.rev_id)"),
]


def profile(con, article):
    row = con.execute(f"""
        WITH tok AS (
            SELECT t.token_id, t.editor, t.n_in, t.n_out,
                   CAST(rr.ts AS TIMESTAMP) AS origin_ts,
                   CAST(a.latest_time AS TIMESTAMP) AS latest_ts
            FROM tokens t
            JOIN revisions rr ON rr.article=t.article AND rr.rev_id=t.o_rev_id
            JOIN articles  a  ON a.article=t.article
            WHERE t.article = ?
        )
        SELECT
            count(*)                                                          AS n_tokens,
            min(origin_ts)                                                    AS first_token_ts,
            median(date_diff('day', origin_ts, latest_ts)) / 365.25          AS median_age_yrs,
            100.0 * sum(CASE WHEN origin_ts >= TIMESTAMP '{OCT7}' THEN 1 ELSE 0 END) / count(*) AS pct_post_oct7,
            100.0 * sum(CASE WHEN origin_ts <  TIMESTAMP '2019-01-01' THEN 1 ELSE 0 END) / count(*) AS pct_pre_2019,
            count(distinct editor)                                            AS distinct_editors,
            100.0 * sum(CASE WHEN n_in>0 OR n_out>0 THEN 1 ELSE 0 END)/count(*) AS pct_churned,
            avg(n_in + n_out)                                                 AS mean_churn_intensity,
            max(n_in + n_out)                                                 AS max_churn_intensity
        FROM tok
    """, [article]).fetchone()
    keys = ["n_tokens", "first_token_ts", "median_age_yrs", "pct_post_oct7", "pct_pre_2019",
            "distinct_editors", "pct_churned", "mean_churn_intensity", "max_churn_intensity"]
    p = dict(zip(keys, row))
    # editor concentration: top-10 editors' share of surviving tokens
    top10 = con.execute("""
        SELECT 100.0 * sum(c) / (SELECT count(*) FROM tokens WHERE article=?) FROM (
            SELECT count(*) c FROM tokens WHERE article=? GROUP BY editor ORDER BY c DESC LIMIT 10
        )""", [article, article]).fetchone()[0]
    p["top10_editor_share"] = top10
    p["tokens_per_editor"] = p["n_tokens"] / p["distinct_editors"]
    return p


def fmt(v):
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def main(contested, control):
    con = duckdb.connect(str(DB_PATH), read_only=True)
    pc = profile(con, contested)
    pk = profile(con, control)
    con.close()

    rows = [
        ("surviving tokens", "n_tokens"),
        ("oldest surviving token", "first_token_ts"),
        ("median age of current text (yrs)", "median_age_yrs"),
        ("% current text authored POST-Oct-7-2023", "pct_post_oct7"),
        ("% current text pre-2019 (long-stable)", "pct_pre_2019"),
        ("distinct editors owning current text", "distinct_editors"),
        ("tokens per editor (concentration)", "tokens_per_editor"),
        ("top-10 editors' share of text (%)", "top10_editor_share"),
        ("% tokens ever churned (in/out)", "pct_churned"),
        ("mean churn intensity (in+out)", "mean_churn_intensity"),
        ("max churn intensity (most-fought token)", "max_churn_intensity"),
    ]
    w = 42
    print(f"\n{'DRIFT PROFILE':<{w}} | {contested:>18} | {control:>18} | separates?")
    print("-" * (w + 46))
    for label, key in rows:
        a, b = pc[key], pk[key]
        sep = ""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b:
            ratio = a / b if b else float("inf")
            if key in ("pct_post_oct7", "mean_churn_intensity", "max_churn_intensity",
                       "tokens_per_editor", "top10_editor_share"):
                sep = f"contested {ratio:.1f}x" if ratio >= 1.3 else ("~same" if 0.77 < ratio < 1.3 else f"control higher")
            elif key in ("median_age_yrs", "pct_pre_2019"):
                sep = "contested younger" if a < b * 0.85 else ("~same" if a < b * 1.15 else "contested older")
        print(f"{label:<{w}} | {fmt(a):>18} | {fmt(b):>18} | {sep}")
    print()


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "Zionism"
    b = sys.argv[2] if len(sys.argv) > 2 else "Photosynthesis"
    main(a, b)

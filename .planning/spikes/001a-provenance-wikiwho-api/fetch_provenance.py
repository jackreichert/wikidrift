"""Spike 001a — token-level provenance via the hosted WikiWho API.

Validates: Given a real enwiki article, when queried via the hosted WikiWho API
(+ MediaWiki Action API for the revision timeline), then we get per-token
{editor, origin_revision, origin_time, in/out churn} — the inputs the §10 drift
engine needs — with no Rust build and no full-history dump.

Writes two tables into a shared DuckDB the drift spike (002) will consume:
  revisions(article, rev_id, ts, user)              -- rev_id -> timestamp/user
  tokens(article, token_id, str, editor, o_rev_id,  -- one row per surviving token
         n_in, n_out)

Usage: uv run python fetch_provenance.py "Article Title" [more titles...]
"""
import sys
import time
import pathlib
import requests
import duckdb

UA = "gh-wiki-spike/0.1 (awesome@rpophesagr.com; wikipedia-drift-detector research)"
WIKIWHO = "https://wikiwho.wmcloud.org/en/api/v1.0.0-beta"
ACTION = "https://en.wikipedia.org/w/api.php"
DB_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


def fetch_tokens(title):
    """Latest-revision surviving tokens, each with origin rev/editor + in/out history."""
    url = (f"{WIKIWHO}/rev_content/{title}/"
           "?editor=true&token_id=true&o_rev_id=true&out=true&in=true")
    r = SESSION.get(url, timeout=180)
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(f"WikiWho failed for {title}: {d.get('message')}")
    rev = d["revisions"][0]
    rev_id = next(iter(rev))
    body = rev[rev_id]
    return d["article_title"], int(d["page_id"]), int(rev_id), body["time"], body["tokens"]


def fetch_timeline(title):
    """rev_id -> (timestamp, user) for the whole page history, via the Action API.

    Serial, paginated (rvlimit=500), maxlag=5 backoff — MediaWiki etiquette.
    """
    out = {}
    params = {
        "action": "query", "format": "json", "prop": "revisions",
        "titles": title, "rvprop": "ids|timestamp|user", "rvlimit": "max",
        "rvdir": "newer", "maxlag": "5", "formatversion": "2",
    }
    calls = 0
    while True:
        r = SESSION.get(ACTION, params=params, timeout=60)
        if r.status_code == 200 and "error" in r.json() and r.json()["error"].get("code") == "maxlag":
            time.sleep(5); continue
        r.raise_for_status()
        data = r.json()
        calls += 1
        pages = data.get("query", {}).get("pages", [])
        for pg in pages:
            for rv in pg.get("revisions", []):
                out[int(rv["revid"])] = (rv["timestamp"], rv.get("user", "<hidden>"))
        if "continue" in data:
            params["rvcontinue"] = data["continue"]["rvcontinue"]
            time.sleep(0.2)
        else:
            break
    return out, calls


def load(con, article, page_id, latest_rev, latest_time, tokens, timeline):
    con.execute("DELETE FROM revisions WHERE article = ?", [article])
    con.execute("DELETE FROM tokens WHERE article = ?", [article])
    con.executemany(
        "INSERT INTO revisions VALUES (?, ?, ?, ?)",
        [(article, rid, ts, user) for rid, (ts, user) in timeline.items()],
    )
    con.executemany(
        "INSERT INTO tokens VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(article, t["token_id"], t["str"], t["editor"], t["o_rev_id"],
          len(t.get("in", [])), len(t.get("out", []))) for t in tokens],
    )
    con.execute(
        "INSERT INTO articles VALUES (?, ?, ?, ?, ?)",
        [article, page_id, latest_rev, latest_time, len(tokens)],
    ) if not con.execute("SELECT 1 FROM articles WHERE article=?", [article]).fetchone() else \
        con.execute("UPDATE articles SET page_id=?, latest_rev=?, latest_time=?, n_tokens=? WHERE article=?",
                    [page_id, latest_rev, latest_time, len(tokens), article])


def ensure_schema(con):
    con.execute("CREATE TABLE IF NOT EXISTS articles(article TEXT, page_id BIGINT, latest_rev BIGINT, latest_time TEXT, n_tokens BIGINT)")
    con.execute("CREATE TABLE IF NOT EXISTS revisions(article TEXT, rev_id BIGINT, ts TEXT, user TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS tokens(article TEXT, token_id BIGINT, str TEXT, editor TEXT, o_rev_id BIGINT, n_in INT, n_out INT)")


def main(titles):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    ensure_schema(con)
    for title in titles:
        t0 = time.time()
        print(f"\n=== {title} ===", flush=True)
        article, page_id, latest_rev, latest_time, tokens = fetch_tokens(title)
        print(f"  wikiwho: {len(tokens):,} surviving tokens @ rev {latest_rev} ({latest_time})", flush=True)
        timeline, calls = fetch_timeline(title)
        print(f"  timeline: {len(timeline):,} revisions in {calls} Action-API calls", flush=True)
        load(con, article, page_id, latest_rev, latest_time, tokens, timeline)
        # proof-of-provenance summary
        matched = con.execute(
            "SELECT count(*) FROM tokens t JOIN revisions r ON t.article=r.article AND t.o_rev_id=r.rev_id WHERE t.article=?",
            [article]).fetchone()[0]
        churned = con.execute("SELECT count(*) FROM tokens WHERE article=? AND (n_in>0 OR n_out>0)", [article]).fetchone()[0]
        print(f"  join: {matched:,}/{len(tokens):,} tokens mapped to a dated origin revision; "
              f"{churned:,} tokens show in/out churn", flush=True)
        print(f"  sample tokens:", flush=True)
        for row in con.execute(
            "SELECT t.str, t.editor, t.o_rev_id, r.ts, t.n_in, t.n_out FROM tokens t "
            "LEFT JOIN revisions r ON t.article=r.article AND t.o_rev_id=r.rev_id "
            "WHERE t.article=? ORDER BY t.token_id LIMIT 6", [article]).fetchall():
            print(f"    {row}", flush=True)
        print(f"  done in {time.time()-t0:.1f}s", flush=True)
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:] or ["Bioglass_45S5"])

"""Provenance layer — DuckDB schema + token/revision fetching from public APIs.

Two data sources, per the design (§3):
  - hosted WikiWho API — per-token authorship + in/out lifecycle for a given revision.
  - MediaWiki Action API — the rev_id -> (timestamp, user) timeline and per-rev byte size.

Everything the L1 drift engine (drift.py) consumes lives in these tables:
  revisions(article, rev_id, ts, user)          -- the timeline
  rev_size(article, rev_id, size)               -- per-rev byte size (vandalism-robust snapshotting)
  rsnap(article, snap_date, snap_rev, token_id, o_rev_id)  -- persistent snapshots (the PWR corpus)
  tokens/articles                               -- 001a's latest-revision provenance (kept for compat)

WikiWho hosted flakiness is handled with fail-fast timeouts + retry/backoff (learned in the base-rate
run). For batch/scale and coverage gaps, the local wikiwho_rs-on-dumps backend feeds the same rsnap
schema via `ingest.py` (both share `snapshot_picks` for identical revision selection).
"""
import time
import urllib.parse
import statistics
from collections import OrderedDict

from . import config

_S = config.session()
# (rev_id, io) -> tokens, process-local LRU cache. Bounded so corpus-scale callers (bootstrap /
# l4.discover looping over many articles in one process) can't grow it without limit (Release It!
# Steady State) — a whole article's analysis needs far fewer than the cap.
_TOK_CAP = 4096
_tok = OrderedDict()


def _tok_put(key, val):
    """Insert into the bounded token cache, evicting the least-recently-used entry past the cap."""
    _tok[key] = val
    if len(_tok) > _TOK_CAP:
        _tok.popitem(last=False)
    return val


# --- schema -----------------------------------------------------------------
def ensure_schema(con):
    con.execute("CREATE TABLE IF NOT EXISTS articles(article TEXT, page_id BIGINT, latest_rev BIGINT, latest_time TEXT, n_tokens BIGINT)")
    con.execute("CREATE TABLE IF NOT EXISTS revisions(article TEXT, rev_id BIGINT, ts TEXT, user TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS tokens(article TEXT, token_id BIGINT, str TEXT, editor TEXT, o_rev_id BIGINT, n_in INT, n_out INT)")
    con.execute("CREATE TABLE IF NOT EXISTS rev_size(article TEXT, rev_id BIGINT, size BIGINT)")
    con.execute("CREATE TABLE IF NOT EXISTS rsnap(article TEXT, snap_date TEXT, snap_rev BIGINT, token_id BIGINT, o_rev_id BIGINT)")


def ensure_indexes(con):
    """Load-bearing: rsnap grows to millions of rows; without these the per-date existence checks
    and interval joins become full-table scans (the perf pathology found in the base-rate run)."""
    ensure_schema(con)
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_rsnap_art_rev ON rsnap(article, snap_rev)",
        "CREATE INDEX IF NOT EXISTS ix_rsnap_art_tok ON rsnap(article, token_id)",
        "CREATE INDEX IF NOT EXISTS ix_rev_art_id ON revisions(article, rev_id)",
        "CREATE INDEX IF NOT EXISTS ix_revsize_art_id ON rev_size(article, rev_id)",
        "CREATE INDEX IF NOT EXISTS ix_tokens_art_tok ON tokens(article, token_id)",
    ):
        try:
            con.execute(stmt)
        except Exception as ex:                          # noqa: BLE001
            # These indexes are load-bearing (the base-rate perf pathology). CREATE INDEX IF NOT EXISTS
            # shouldn't raise on a normal re-run, so any failure is real — surface it, never swallow it.
            print(f"  !! WARNING: index not created ({stmt.split(' ON ')[0].split()[-1]}): {ex}", flush=True)


# --- Action API: timeline + sizes -------------------------------------------
def ensure_sizes(con, article):
    """Populate the revision timeline (rev_id->ts/user) and per-rev size. Commits per page so
    an interrupted run can resume: re-entry continues from the last stored revision instead of
    re-downloading the entire history."""
    ensure_schema(con)
    latest = con.execute("SELECT max(rev_id) FROM revisions WHERE article=?", [article]).fetchone()[0]

    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
              "titles": article, "rvprop": "ids|timestamp|user|size", "rvlimit": "max", "rvdir": "newer", "maxlag": "5"}
    if latest:
        params["rvstartid"] = latest   # resume from last known rev (inclusive — deduped below)

    # Load existing IDs only when resuming so we can skip the boundary rev returned by rvstartid
    seen = ({r[0] for r in con.execute("SELECT rev_id FROM revisions WHERE article=?", [article]).fetchall()}
            if latest else set())
    total = len(seen)

    while True:
        d = config.get_json_retrying(_S, config.ACTION, params=params)   # network/Action-API can be flaky
        revrows, szrows = [], []
        for pg in d.get("query", {}).get("pages", []):
            for rv in pg.get("revisions", []):
                rid = int(rv["revid"])
                if rid in seen:
                    continue
                seen.add(rid)
                revrows.append((article, rid, rv["timestamp"], rv.get("user", "<hidden>")))
                szrows.append((article, rid, int(rv.get("size", 0))))
        if revrows:
            con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", revrows)
            con.executemany("INSERT INTO rev_size VALUES (?,?,?)", szrows)
            total += len(revrows)
        print(f"\r  history: {total:,} revisions…", end="", flush=True)
        if "continue" in d:
            params.pop("rvstartid", None)   # rvcontinue supersedes rvstartid
            params["rvcontinue"] = d["continue"]["rvcontinue"]
        else:
            break
    print(f"\r  history: {total:,} revisions" + " " * 10, flush=True)


# --- WikiWho: per-revision tokens -------------------------------------------
def tokens_at(article, rev_id, io=False):
    """Surviving tokens of `article` at `rev_id`, cached. io=True also returns in/out lifecycle."""
    key = (rev_id, io)
    if key in _tok:
        _tok.move_to_end(key)                      # LRU: mark as recently used
        return _tok[key]
    extra = "&out=true&in=true" if io else "&out=false&in=false"
    url = f"{config.WIKIWHO}/rev_content/{urllib.parse.quote(article, safe='')}/{rev_id}/?token_id=true&o_rev_id=true{extra}"
    for attempt in range(4):                       # retry: WikiWho throws transient errors under load
        try:
            d = _S.get(url, timeout=25).json()     # fail fast; WMCloud is flaky, don't hang 180s per call
            if d.get("revisions"):
                rev = d["revisions"][0]; rid = next(iter(rev))
                return _tok_put(key, rev[rid]["tokens"])
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return _tok_put(key, [])                        # unavailable after retries -> caller skips


# --- snapshotting -----------------------------------------------------------
def persistent_rev(con, article, date, window_days=21):
    """Pick a revision near `date` whose size ~ the local median (rejects transient blanking)."""
    rows = con.execute("""
        SELECT r.rev_id, r.ts, z.size,
               abs(date_diff('day', CAST(r.ts AS TIMESTAMP), CAST(? AS TIMESTAMP))) AS dd
        FROM revisions r JOIN rev_size z ON z.article=r.article AND z.rev_id=r.rev_id
        WHERE r.article=? AND abs(date_diff('day', CAST(r.ts AS TIMESTAMP), CAST(? AS TIMESTAMP))) <= ?
        ORDER BY dd
    """, [date + " 00:00:00", article, date + " 00:00:00", window_days]).fetchall()
    if not rows:
        r = con.execute("""SELECT r.rev_id, r.ts FROM revisions r
            WHERE r.article=? AND r.ts<=? ORDER BY r.ts DESC LIMIT 1""", [article, date + "T00:00:00Z"]).fetchone()
        return r
    med = statistics.median([s for _, _, s, _ in rows])
    for rev_id, ts, size, _ in rows:               # nearest-to-date rev whose size is within 25% of median
        if med and abs(size - med) <= 0.25 * med:
            return (rev_id, ts)
    return (rows[0][0], rows[0][1])


def snapshot_picks(con, article):
    """The persistent-revision snapshot dates + rev_ids (adaptive cadence, size ~ local median).
    Backend-agnostic selection shared by build_snapshots (hosted WikiWho) and ingest (local wikiwho_rs),
    so both populate rsnap from the *same* revisions."""
    first = con.execute("SELECT min(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    last = con.execute("SELECT max(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    total_revs = con.execute("SELECT count(*) FROM revisions WHERE article=?", [article]).fetchone()[0]
    if not first or not last:
        return []
    # adaptive cadence: annual for large articles (keeps rsnap small); binary search refines timing anyway
    months = (1, 7) if total_revs <= 8000 else (1,)
    dates = [f"{y}-{m:02d}-01" for y in range(int(first[:4]), int(last[:4]) + 1) for m in months]
    picks = []
    for d in dates:
        pr = persistent_rev(con, article, d)
        if not pr or (picks and pr[0] == picks[-1][1]):
            continue
        picks.append((d, pr[0]))
    return picks


def _pbar(done, total, width=20):
    filled = int(width * done / total) if total else width
    return "█" * filled + "░" * (width - filled)


def build_snapshots(con, article):
    """Build persistent-revision snapshots into rsnap from the HOSTED WikiWho API (polite)."""
    ensure_schema(con)
    picks = snapshot_picks(con, article)
    n = len(picks)
    snaps = []
    for i, (d, rev_id) in enumerate(picks, 1):
        cached = bool(con.execute("SELECT 1 FROM rsnap WHERE article=? AND snap_rev=?", [article, rev_id]).fetchone())
        label = "cached " if cached else "fetch  "
        print(f"\r  snapshots [{_pbar(i, n)}] {i}/{n}  {d}  {label}", end="", flush=True)
        if not cached:
            toks = tokens_at(article, rev_id)
            if not toks:
                continue                            # WikiWho couldn't serve this revision — skip snapshot
            con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)",
                            [(article, d, rev_id, t["token_id"], t["o_rev_id"]) for t in toks])
            time.sleep(0.3)                         # be polite to the WikiWho API
        snaps.append((d, rev_id))
    if n:
        print(f"\r  snapshots [{_pbar(n, n)}] {len(snaps)}/{n} loaded" + " " * 20, flush=True)
    return snaps

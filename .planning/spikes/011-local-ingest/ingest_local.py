"""Spike 011 — local wikiwho_rs ingestion for the benchmark must-flag articles.

Populates the SAME rsnap schema the hosted path (drift.build_snapshots) writes, but from the local
`wikiwho` engine on the article's own full-history XML — no hosted WikiWho, robust to its coverage gaps
(the Poland-WWII articles the hosted service does not process). This is the documented "local wikiwho_rs
on dumps for batch/scale" path; here it runs per-article (targeted) rather than over a full 200GB dump.

Pipeline per article:
  1. provenance.ensure_sizes — revisions(rev_id,ts,user) + rev_size(size) via the Action API (metadata).
  2. fetch full-history wikitext (Action-API content paging) -> a MediaWiki export-0.11 XML file.
  3. pick persistent-revision snapshots (size ~ local median) over the date grid — the SAME selection as
     drift.build_snapshots, minus the hosted token fetch.
  4. run the `snapshot-tokens` Rust helper on the XML for exactly those rev_ids -> token sets (uid, origin)
     for the true historical state (incl. tokens later deleted — what the loss metric needs).
  5. load rsnap(article, snap_date, snap_rev, token_id=uid, o_rev_id=origin).

Usage: uv run python ingest_local.py "Naliboki massacre" ["Jedwabne pogrom" ...]
"""
import sys
import time
import pathlib
import subprocess
import xml.sax.saxutils as sax

import duckdb

from wikidrift import config, provenance

HERE = pathlib.Path(__file__).resolve().parent
BIN = HERE / "snapshot-tokens" / "target" / "release" / "snapshot-tokens"
XML_DIR = HERE / "xml"
S = config.session()


def fetch_history_xml(article):
    """Assemble a MediaWiki export-0.11 XML of the article's FULL history (oldest-first) from the
    Action API's revision content. Cached to disk (immutable). Returns (path, page_id)."""
    XML_DIR.mkdir(parents=True, exist_ok=True)
    safe = article.replace("/", "_").replace(" ", "_")
    out = XML_DIR / f"{safe}.xml"
    # page id / title / ns
    info = S.get(config.ACTION, params={"action": "query", "format": "json", "formatversion": "2",
                 "titles": article, "prop": "info"}, timeout=30).json()
    pg = info["query"]["pages"][0]
    page_id, ns, title = pg["pageid"], pg.get("ns", 0), pg["title"]
    if out.exists() and out.stat().st_size > 0:
        print(f"  xml cached: {out.name}", flush=True)
        return out, page_id

    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
              "titles": article, "rvprop": "ids|timestamp|user|content|flags",
              "rvslots": "main", "rvlimit": "max", "rvdir": "newer", "maxlag": "5"}
    n = 0
    with open(out, "w", encoding="utf-8") as fh:
        fh.write('<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/" version="0.11" xml:lang="en">\n')
        fh.write('  <siteinfo><sitename>Wikipedia</sitename>\n')
        fh.write('    <namespaces><namespace key="0" case="first-letter" /></namespaces>\n  </siteinfo>\n')
        fh.write(f'  <page>\n    <title>{sax.escape(title)}</title>\n    <ns>{ns}</ns>\n    <id>{page_id}</id>\n')
        while True:
            for attempt in range(4):
                try:
                    d = S.get(config.ACTION, params=params, timeout=60).json()
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(1.5 * (attempt + 1))
            for pgd in d.get("query", {}).get("pages", []):
                for rv in pgd.get("revisions", []):
                    slot = rv.get("slots", {}).get("main", {})
                    if slot.get("texthidden") or "content" not in slot:
                        continue                       # deleted/suppressed text — skip (rare)
                    text = slot["content"]
                    user = rv.get("user", "hidden")
                    contrib = (f"<ip>{sax.escape(user)}</ip>" if rv.get("anon")
                               else f"<username>{sax.escape(user)}</username>")
                    fh.write(f'    <revision>\n      <id>{rv["revid"]}</id>\n'
                             f'      <timestamp>{rv["timestamp"]}</timestamp>\n'
                             f'      <contributor>{contrib}</contributor>\n'
                             f'      <text xml:space="preserve">{sax.escape(text)}</text>\n    </revision>\n')
                    n += 1
            if "continue" in d:
                params["rvcontinue"] = d["continue"]["rvcontinue"]
                print(f"    ...{n:,} revisions", flush=True)
            else:
                break
        fh.write('  </page>\n</mediawiki>\n')
    print(f"  xml written: {out.name} ({n:,} revisions)", flush=True)
    return out, page_id


def snapshot_rev_ids(con, article):
    """The persistent-revision snapshot dates+rev_ids — SAME selection as drift.build_snapshots
    (adaptive cadence, size ~ local median), but without any token fetch."""
    first = con.execute("SELECT min(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    last = con.execute("SELECT max(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]
    total = con.execute("SELECT count(*) FROM revisions WHERE article=?", [article]).fetchone()[0]
    months = (1, 7) if total <= 8000 else (1,)
    dates = [f"{y}-{m:02d}-01" for y in range(int(first[:4]), int(last[:4]) + 1) for m in months]
    picks, seen = [], set()
    for dstr in dates:
        pr = provenance.persistent_rev(con, article, dstr)
        if not pr or pr[0] in seen:
            continue
        seen.add(pr[0])
        picks.append((dstr, pr[0]))
    return picks


def run_helper(xml_path, rev_ids):
    """snapshot-tokens <xml> <csv rev ids> -> {rev_id: [(uid, origin_id), ...]}."""
    if not BIN.exists():
        raise SystemExit(f"helper not built: {BIN} (cargo build --release in snapshot-tokens/)")
    csv = ",".join(str(r) for r in rev_ids)
    proc = subprocess.run([str(BIN), str(xml_path), csv], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"snapshot-tokens failed: {proc.stderr[-2000:]}")
    out = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        rid = int(parts[0])
        toks = []
        for p in parts[1:]:
            uid, origin = p.split(":")
            toks.append((int(uid), int(origin)))
        out[rid] = toks
    return out


def ingest(con, article, force=False):
    print(f"=== INGEST (local): {article} ===", flush=True)
    provenance.ensure_sizes(con, article)
    provenance.ensure_indexes(con)
    existing = con.execute("SELECT count(distinct snap_rev) FROM rsnap WHERE article=?", [article]).fetchone()[0]
    if existing >= 3 and not force:
        print(f"  already ingested ({existing} snapshots) — skipping (use --force to re-ingest via local)"); return
    if force and existing:
        print(f"  --force: replacing {existing} existing snapshots with local wikiwho_rs data", flush=True)
    xml_path, _ = fetch_history_xml(article)
    picks = snapshot_rev_ids(con, article)
    print(f"  {len(picks)} persistent snapshots to extract", flush=True)
    tokens_by_rev = run_helper(xml_path, [r for _, r in picks])
    rows = []
    for dstr, rid in picks:
        for uid, origin in tokens_by_rev.get(rid, []):
            rows.append((article, dstr, rid, uid, origin))
    con.execute("DELETE FROM rsnap WHERE article=?", [article])
    con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", rows)
    got = len({r for _, r in picks if r in tokens_by_rev})
    print(f"  rsnap loaded: {len(rows):,} token-rows across {got}/{len(picks)} snapshots", flush=True)


def main(articles, force=False):
    con = duckdb.connect(str(config.DB))
    for a in articles:
        try:
            ingest(con, a, force=force)
        except SystemExit as e:
            print(f"  !! {e}")
        except Exception as e:
            print(f"  !! {a}: {e}", flush=True)
    con.close()


if __name__ == "__main__":
    force = "--force" in sys.argv
    arts = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(arts or ["Naliboki massacre"], force=force)

"""Local wikiwho_rs-on-dumps ingestion backend (promoted from spike 011).

Populates the SAME rsnap schema as the hosted path (`provenance.build_snapshots`), but from the
article's own full-history XML via the local `wikiwho` engine — no hosted WikiWho. This is the
documented backend for two cases the hosted API can't cover:
  - **Coverage gaps** — articles the hosted service will not process (e.g. the Poland-WWII slate).
  - **Batch / scale** — corpus-scale ingestion off dumps (the L4 substrate).

The `snapshot-tokens` Rust helper (tools/snapshot-tokens) emits token authorship for specific
HISTORICAL revisions — the true snapshot state including tokens later deleted, which the loss metric
needs. It became viable on real articles once the wikiwho_rs dump-parser entity bug was fixed
(Schuwi/wikiwho_rs PR #44, found + fixed by this project).

Pipeline per article:
  1. provenance.ensure_sizes — revisions(rev_id, ts, user) + rev_size(size) via the Action API.
  2. assemble the full-history wikitext into a MediaWiki export-0.11 XML (cached, immutable).
  3. provenance.snapshot_picks — the SAME persistent-revision selection the hosted path uses.
  4. run snapshot-tokens on the XML for exactly those rev_ids -> per-revision (uid, origin) token sets.
  5. load rsnap(article, snap_date, snap_rev, token_id=uid, o_rev_id=origin).

    uv run wikidrift ingest "Naliboki massacre" ["Jedwabne pogrom" ...] [--force]
"""
import subprocess
import xml.sax.saxutils as sax

import duckdb

from . import config, provenance
from .corpus import Corpus

_S = config.session()


def fetch_history_xml(article):
    """Assemble a MediaWiki export-0.11 XML of the article's FULL history (oldest-first) from the
    Action API's revision content. Cached to disk (immutable). Returns (path, page_id)."""
    config.XML_CACHE.mkdir(parents=True, exist_ok=True)
    safe = config.slugify(article)
    out = config.XML_CACHE / f"{safe}.xml"
    info = _S.get(config.ACTION, params={"action": "query", "format": "json", "formatversion": "2",
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
            d = config.get_json_retrying(_S, config.ACTION, params=params, timeout=60)
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


def run_helper(xml_path, rev_ids):
    """snapshot-tokens <xml> <csv rev ids> -> {rev_id: [(uid, origin_id), ...]}."""
    binp = config.SNAPSHOT_TOKENS_BIN
    if not binp.exists():
        raise SystemExit(f"snapshot-tokens helper not built: {binp}\n"
                         f"  build it with: (cd tools/snapshot-tokens && cargo build --release)")
    csv = ",".join(str(r) for r in rev_ids)
    proc = subprocess.run([str(binp), str(xml_path), csv], capture_output=True, text=True)
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
    """Ingest one article's persistent-revision snapshots into rsnap via the local engine."""
    print(f"=== INGEST (local wikiwho_rs): {article} ===", flush=True)
    provenance.ensure_sizes(con, article)
    provenance.ensure_indexes(con)
    existing = Corpus(con).snapshot_count(article)
    if existing >= 3 and not force:
        print(f"  already ingested ({existing} snapshots) — skip (use --force to re-ingest via local)")
        return
    if force and existing:
        print(f"  --force: replacing {existing} existing snapshots with local wikiwho_rs data", flush=True)
    xml_path, _ = fetch_history_xml(article)
    picks = provenance.snapshot_picks(con, article)
    print(f"  {len(picks)} persistent snapshots to extract", flush=True)
    tokens_by_rev = run_helper(xml_path, [r for _, r in picks])
    rows = [(article, dstr, rid, uid, origin)
            for dstr, rid in picks for uid, origin in tokens_by_rev.get(rid, [])]
    got = len({r for _, r in picks if r in tokens_by_rev})
    # Guard: if the helper returned nothing (crash / XML-parse miss / all picks absent), do NOT wipe the
    # existing good snapshots — a --force re-ingest that produced 0 rows would otherwise silently zero the
    # article. Replace only when we actually have replacement data, and do it atomically so a mid-write
    # failure can't leave the article half-deleted (PEAA Unit of Work).
    if not rows:
        print(f"  !! extracted 0 token-rows ({got}/{len(picks)} snapshots) — keeping existing rsnap, NOT wiping",
              flush=True)
        return
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM rsnap WHERE article=?", [article])
        con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", rows)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print(f"  rsnap loaded: {len(rows):,} token-rows across {got}/{len(picks)} snapshots", flush=True)


def ingest_articles(articles, force=False):
    """Ingest one or more articles. After this, offline `analyze`/`validate` run on the local data."""
    con = duckdb.connect(str(config.DB))
    try:
        for a in articles:
            try:
                ingest(con, a, force=force)
            except SystemExit as e:
                print(f"  !! {e}")
            except Exception as e:                     # noqa: BLE001 — degrade gracefully per article
                print(f"  !! {a}: {e}", flush=True)
    finally:
        con.close()

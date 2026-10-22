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
  4. run snapshot-tokens on the XML for those rev_ids in RAM-bounded batches -> per-revision
     (uid, origin) token sets; stream helper stdout and INSERT into rsnap incrementally.
  5. load rsnap(article, snap_date, snap_rev, token_id=uid, o_rev_id=origin).

    uv run wikidrift ingest "Naliboki massacre" ["Jedwabne pogrom" ...] [--force]

Memory (GitHub Actions `ubuntu-latest` ~7GB): never hold the full token universe in Python.
`WIKIDRIFT_INGEST_SNAP_BATCH` (default 1) controls how many picks share one helper invocation;
each invocation truncates analysis after the newest target in that batch.
"""
from __future__ import annotations

import os
import subprocess
import xml.sax.saxutils as sax
from pathlib import Path

import duckdb

from . import config, provenance
from .corpus import Corpus

# Flush staged INSERT batches so we never hold a multi-snap token universe in Python RAM.
_STAGE_INSERT_CHUNK = 10_000

_S = config.session()

# How many persistent-snapshot picks share one snapshot-tokens process. Default 1 keeps Python
# peak to one snap's token rows; the helper still needs history up through that pick.
_DEFAULT_SNAP_BATCH = 1


def _snap_batch_size() -> int:
    raw = os.environ.get("WIKIDRIFT_INGEST_SNAP_BATCH", str(_DEFAULT_SNAP_BATCH)).strip()
    try:
        n = int(raw)
    except ValueError:
        n = _DEFAULT_SNAP_BATCH
    return max(1, n)


def xml_export_complete(path: Path) -> bool:
    """True when a cached MediaWiki export looks closed (not a killed mid-write).

    `fetch_history_xml` writes `</page></mediawiki>` only after all revisions. A runner OOM/kill
    mid-write leaves a non-empty file that DumpParser rejects with Eof — treat as missing.
    """
    if not path.exists() or path.stat().st_size <= 0:
        return False
    # Read a small tail; export footer is ASCII and short.
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 256))
        tail = fh.read().decode("utf-8", errors="replace")
    return "</mediawiki>" in tail


def fetch_history_xml(article):
    """Assemble a MediaWiki export-0.11 XML of the article's FULL history (oldest-first) from the
    Action API's revision content. Cached to disk (immutable). Returns (path, page_id)."""
    resolved = provenance.resolve_article_title(article)
    article = resolved.canonical_title
    config.XML_CACHE.mkdir(parents=True, exist_ok=True)
    safe = config.slugify(article)
    out = config.XML_CACHE / f"{safe}.xml"
    page_id, title = resolved.page_id, resolved.canonical_title
    ns = 0
    if xml_export_complete(out):
        print(f"  xml cached: {out.name}", flush=True)
        return out, page_id
    if out.exists():
        print(f"  xml incomplete (no </mediawiki>) — re-fetching {out.name}", flush=True)
        out.unlink(missing_ok=True)

    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
              "redirects": 1, "titles": article, "rvprop": "ids|timestamp|user|content|flags",
              "rvslots": "main", "rvlimit": "max", "rvdir": "newer", "maxlag": "5"}
    n = 0
    # Write to a temp path then rename so a kill mid-fetch cannot leave a "complete-looking"
    # partial beside a future incomplete-tail check miss.
    tmp = out.with_suffix(out.suffix + ".partial")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
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
        tmp.replace(out)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    print(f"  xml written: {out.name} ({n:,} revisions)", flush=True)
    return out, page_id


def _parse_helper_line(line: str):
    """Parse one snapshot-tokens stdout line -> (rev_id, [(uid, origin), ...])."""
    parts = line.split("\t")
    rid = int(parts[0])
    toks = []
    for p in parts[1:]:
        uid, origin = p.split(":")
        toks.append((int(uid), int(origin)))
    return rid, toks


def run_helper(xml_path, rev_ids):
    """snapshot-tokens <xml> <csv rev ids> -> {rev_id: [(uid, origin_id), ...]}.

    Streams stdout line-by-line (never `capture_output` of the full token dump). An empty
    rev_ids list is a no-op — the helper treats empty CSV as empty, not "all".
    """
    binp = config.SNAPSHOT_TOKENS_BIN
    if not binp.exists():
        raise SystemExit(f"snapshot-tokens helper not built: {binp}\n"
                         f"  build it with: (cd tools/snapshot-tokens && cargo build --release)")
    rev_ids = list(rev_ids)
    if not rev_ids:
        return {}
    csv = ",".join(str(r) for r in rev_ids)
    proc = subprocess.Popen(
        [str(binp), str(xml_path), csv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    out = {}
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            rid, toks = _parse_helper_line(line)
            out[rid] = toks
            # Free as we go when caller only needs incremental insert — retained here for the
            # dict API used by tests; ingest() uses iter_helper_rows for lower peak.
        stderr = proc.stderr.read()
    finally:
        proc.wait()
    if proc.returncode != 0:
        raise SystemExit(f"snapshot-tokens failed: {stderr[-2000:]}")
    if stderr.strip():
        for err_line in stderr.strip().splitlines()[-5:]:
            print(f"  [snapshot-tokens] {err_line}", flush=True)
    return out


def iter_helper_rows(xml_path, article, picks_batch):
    """Yield rsnap row tuples for one pick batch without buffering all tokens.

    picks_batch: list of (dstr, rev_id).
    """
    rev_ids = [r for _, r in picks_batch]
    if not rev_ids:
        return
    binp = config.SNAPSHOT_TOKENS_BIN
    if not binp.exists():
        raise SystemExit(f"snapshot-tokens helper not built: {binp}\n"
                         f"  build it with: (cd tools/snapshot-tokens && cargo build --release)")
    date_by_rev = {r: d for d, r in picks_batch}
    csv = ",".join(str(r) for r in rev_ids)
    proc = subprocess.Popen(
        [str(binp), str(xml_path), csv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    got = set()
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            rid, toks = _parse_helper_line(line)
            dstr = date_by_rev.get(rid)
            if dstr is None:
                continue
            got.add(rid)
            for uid, origin in toks:
                yield (article, dstr, rid, uid, origin)
        stderr = proc.stderr.read()
    finally:
        proc.wait()
    if proc.returncode != 0:
        raise SystemExit(f"snapshot-tokens failed: {stderr[-2000:]}")
    if stderr.strip():
        for err_line in stderr.strip().splitlines()[-5:]:
            print(f"  [snapshot-tokens] {err_line}", flush=True)
    missing = [r for r in rev_ids if r not in got]
    if missing:
        print(f"  !! helper missed {len(missing)} pick(s) in batch (e.g. {missing[0]})", flush=True)


def _batched(seq, size):
    batch = []
    for item in seq:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def ingest(con, article, force=False):
    """Ingest one article's persistent-revision snapshots into rsnap via the local engine."""
    resolved = provenance.resolve_article_title(article)
    provenance.record_article_identity(con, resolved)
    article = resolved.canonical_title
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
    if not picks:
        print("  no snapshot picks — nothing to extract", flush=True)
        return

    batch_size = _snap_batch_size()
    batches = list(_batched(picks, batch_size))
    print(f"  extracting in {len(batches)} batch(es) of ≤{batch_size} (WIKIDRIFT_INGEST_SNAP_BATCH)",
          flush=True)

    # Guard: if the helper returned nothing (crash / XML-parse miss / all picks absent), do NOT wipe the
    # existing good snapshots — a --force re-ingest that produced 0 rows would otherwise silently zero the
    # article. Replace only when we actually have replacement data, and do it atomically so a mid-write
    # failure can't leave the article half-deleted (PEAA Unit of Work).
    con.execute("BEGIN")
    try:
        # Stage into a temp table so we never DELETE surviving rows until a batch produced data.
        con.execute("""
            CREATE OR REPLACE TEMP TABLE _rsnap_stage (
                article VARCHAR, snap_date VARCHAR, snap_rev BIGINT,
                token_id BIGINT, o_rev_id BIGINT
            )
        """)
        got = set()
        row_count = 0
        for i, batch in enumerate(batches, 1):
            print(f"  batch {i}/{len(batches)}: {len(batch)} pick(s) "
                  f"(rev {batch[0][1]}..{batch[-1][1]})", flush=True)
            batch_rows = 0
            chunk = []
            for row in iter_helper_rows(xml_path, article, batch):
                chunk.append(row)
                got.add(row[2])
                batch_rows += 1
                row_count += 1
                if len(chunk) >= _STAGE_INSERT_CHUNK:
                    con.executemany("INSERT INTO _rsnap_stage VALUES (?,?,?,?,?)", chunk)
                    chunk.clear()
            if chunk:
                con.executemany("INSERT INTO _rsnap_stage VALUES (?,?,?,?,?)", chunk)
                chunk.clear()
            print(f"    → {batch_rows:,} token-rows", flush=True)

        if row_count == 0:
            con.execute("ROLLBACK")
            print(f"  !! extracted 0 token-rows ({len(got)}/{len(picks)} snapshots) — "
                  f"keeping existing rsnap, NOT wiping", flush=True)
            return

        con.execute("DELETE FROM rsnap WHERE article=?", [article])
        con.execute("INSERT INTO rsnap SELECT * FROM _rsnap_stage")
        integrity = provenance.refresh_snapshot_integrity(con, article)
        provenance.refresh_stable_endpoint(con, article)
        quarantined = [receipt for receipt in integrity if receipt["status"] == "quarantined"]
        complete = bool(picks) and len(got) == len(picks) and not quarantined
        reason = None
        if quarantined:
            reason = f"{len(quarantined)} snapshot(s) failed integrity checks"
        elif not complete:
            reason = f"loaded {len(got)} of {len(picks)} expected snapshots"
        provenance.record_source_state(
            con,
            article,
            source_status="current_complete" if complete else "partial",
            expected_snapshots=len(picks),
            loaded_snapshots=len(got),
            reason=reason,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print(f"  rsnap loaded: {row_count:,} token-rows across {len(got)}/{len(picks)} snapshots",
          flush=True)


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

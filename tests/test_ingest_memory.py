"""Unit tests for RAM-safe local ingest (Actions ~7GB runners).

Does not run a full large-article ingest. Covers XML completeness, empty-pick
safety, batching, and streaming helper parse — the levers that stop cold-start
OOM after `xml written`.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from wikidrift import ingest


class XmlExportComplete(unittest.TestCase):
    def test_missing_is_incomplete(self):
        self.assertFalse(ingest.xml_export_complete(Path("/no/such/file.xml")))

    def test_partial_export_without_footer(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.xml"
            p.write_text("<mediawiki><page><revision></revision>\n", encoding="utf-8")
            self.assertFalse(ingest.xml_export_complete(p))

    def test_closed_export_is_complete(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.xml"
            p.write_text(
                "<mediawiki><page></page>\n</mediawiki>\n",
                encoding="utf-8",
            )
            self.assertTrue(ingest.xml_export_complete(p))


class HelperParseAndBatch(unittest.TestCase):
    def test_parse_helper_line(self):
        rid, toks = ingest._parse_helper_line("42\t1:10\t2:20")
        self.assertEqual(rid, 42)
        self.assertEqual(toks, [(1, 10), (2, 20)])

    def test_batched_chunks(self):
        self.assertEqual(
            list(ingest._batched([1, 2, 3, 4, 5], 2)),
            [[1, 2], [3, 4], [5]],
        )

    def test_snap_batch_size_env(self):
        with patch.dict(os.environ, {"WIKIDRIFT_INGEST_SNAP_BATCH": "3"}):
            self.assertEqual(ingest._snap_batch_size(), 3)
        with patch.dict(os.environ, {"WIKIDRIFT_INGEST_SNAP_BATCH": "0"}):
            self.assertEqual(ingest._snap_batch_size(), 1)
        with patch.dict(os.environ, {"WIKIDRIFT_INGEST_SNAP_BATCH": "nope"}):
            self.assertEqual(ingest._snap_batch_size(), 1)

    def test_run_helper_empty_rev_ids_is_noop(self):
        # Must not invoke the binary with an empty CSV (historically meant "all"),
        # and must not require the binary to exist (CI has no cargo build).
        missing_bin = Path("/nonexistent/snapshot-tokens-ci-guard")
        with patch.object(ingest.config, "SNAPSHOT_TOKENS_BIN", missing_bin), \
             patch("wikidrift.ingest.subprocess.Popen") as popen:
            self.assertEqual(ingest.run_helper(Path("x.xml"), []), {})
            popen.assert_not_called()

    def test_run_helper_streams_stdout(self):
        fake = MagicMock()
        fake.stdout = iter(["10\t1:1\t2:2\n", "20\t3:3\n"])
        fake.stderr = MagicMock()
        fake.stderr.read.return_value = "snapshot-tokens: analysed 1 page(s)\n"
        fake.returncode = 0
        fake.wait.return_value = 0
        with tempfile.TemporaryDirectory() as td:
            bin_path = Path(td) / "snapshot-tokens"
            bin_path.write_text("#!/bin/true\n", encoding="utf-8")
            bin_path.chmod(0o755)
            with patch.object(ingest.config, "SNAPSHOT_TOKENS_BIN", bin_path), \
                 patch("wikidrift.ingest.subprocess.Popen", return_value=fake) as popen:
                out = ingest.run_helper(Path("page.xml"), [10, 20])
        popen.assert_called_once()
        args = popen.call_args[0][0]
        self.assertEqual(args[2], "10,20")
        self.assertEqual(out[10], [(1, 1), (2, 2)])
        self.assertEqual(out[20], [(3, 3)])


class IngestChunkedLoad(unittest.TestCase):
    def test_ingest_stages_batches_without_full_token_dict(self):
        """Force two batches; ensure iter_helper_rows is called per batch and rsnap filled."""
        picks = [("2020-01-01", 1), ("2021-01-01", 2), ("2022-01-01", 3)]

        def fake_rows(xml_path, article, batch):
            for dstr, rid in batch:
                yield (article, dstr, rid, 100 + rid, rid)

        con = MagicMock()
        # BEGIN / CREATE / INSERTs / DELETE / SELECT / COMMIT — return innocuous values
        con.execute.return_value = None
        con.executemany.return_value = None

        with patch.object(ingest.provenance, "resolve_article_title") as resolve, \
             patch.object(ingest.provenance, "record_article_identity"), \
             patch.object(ingest.provenance, "ensure_sizes"), \
             patch.object(ingest.provenance, "ensure_indexes"), \
             patch.object(ingest.Corpus, "snapshot_count", return_value=0), \
             patch.object(ingest, "fetch_history_xml", return_value=(Path("x.xml"), 1)), \
             patch.object(ingest.provenance, "snapshot_picks", return_value=picks), \
             patch.object(ingest, "iter_helper_rows", side_effect=fake_rows) as rows_iter, \
             patch.object(ingest.provenance, "refresh_snapshot_integrity", return_value=[]), \
             patch.object(ingest.provenance, "refresh_stable_endpoint"), \
             patch.object(ingest.provenance, "record_source_state"), \
             patch.dict(os.environ, {"WIKIDRIFT_INGEST_SNAP_BATCH": "2"}):
            resolve.return_value = MagicMock(canonical_title="Demo", page_id=1)
            ingest.ingest(con, "Demo", force=False)

        self.assertEqual(rows_iter.call_count, 2)  # batches [1,2] and [3]
        # DELETE only after staging produced rows
        delete_calls = [c for c in con.execute.call_args_list
                        if c.args and "DELETE FROM rsnap" in str(c.args[0])]
        self.assertEqual(len(delete_calls), 1)
        commit_calls = [c for c in con.execute.call_args_list
                        if c.args and c.args[0] == "COMMIT"]
        self.assertEqual(len(commit_calls), 1)

    def test_ingest_zero_rows_keeps_existing(self):
        picks = [("2020-01-01", 1)]
        con = MagicMock()
        with patch.object(ingest.provenance, "resolve_article_title") as resolve, \
             patch.object(ingest.provenance, "record_article_identity"), \
             patch.object(ingest.provenance, "ensure_sizes"), \
             patch.object(ingest.provenance, "ensure_indexes"), \
             patch.object(ingest.Corpus, "snapshot_count", return_value=5), \
             patch.object(ingest, "fetch_history_xml", return_value=(Path("x.xml"), 1)), \
             patch.object(ingest.provenance, "snapshot_picks", return_value=picks), \
             patch.object(ingest, "iter_helper_rows", return_value=iter(())), \
             patch.dict(os.environ, {"WIKIDRIFT_INGEST_SNAP_BATCH": "1"}):
            resolve.return_value = MagicMock(canonical_title="Demo", page_id=1)
            ingest.ingest(con, "Demo", force=True)
        delete_calls = [c for c in con.execute.call_args_list
                        if c.args and "DELETE FROM rsnap" in str(c.args[0])]
        self.assertEqual(delete_calls, [])
        rollback = [c for c in con.execute.call_args_list
                    if c.args and c.args[0] == "ROLLBACK"]
        self.assertTrue(rollback)


if __name__ == "__main__":
    unittest.main()

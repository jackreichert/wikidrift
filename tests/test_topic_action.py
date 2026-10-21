"""Unit tests for Topic Action cold-start / delta corpus policy."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wikidrift import provenance
from wikidrift.topic_action import plan_update_corpus, resolve_topic_for_action


def _load_topic_shard_gate():
    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / "tools" / "topic_shard_gate.py"
    spec = importlib.util.spec_from_file_location("topic_shard_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


topic_shard_gate = _load_topic_shard_gate()


class PlanUpdateCorpusTests(unittest.TestCase):
    def test_delta_hosted_no_ingest(self):
        plan = plan_update_corpus(shard_present=True, corpus_backend="hosted")
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["kind"], "delta")
        self.assertFalse(plan["need_ingest"])

    def test_delta_ingest_still_runs_ingest(self):
        plan = plan_update_corpus(shard_present=True, corpus_backend="ingest")
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["kind"], "delta")
        self.assertTrue(plan["need_ingest"])

    def test_cold_start_ingest_ok(self):
        plan = plan_update_corpus(shard_present=False, corpus_backend="ingest")
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["kind"], "cold_start")
        self.assertTrue(plan["need_ingest"])
        self.assertIn("Cold-start", plan["message"])

    def test_cold_start_hosted_blocked(self):
        plan = plan_update_corpus(shard_present=False, corpus_backend="hosted")
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["kind"], "cold_start_blocked")
        self.assertTrue(plan["need_ingest"])
        self.assertIn("corpus_backend=ingest", plan["message"])

    def test_backend_normalized(self):
        plan = plan_update_corpus(shard_present=False, corpus_backend=" Ingest ")
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["kind"], "cold_start")

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            plan_update_corpus(shard_present=True, corpus_backend="wikiwho")


class ResolveTopicForActionTests(unittest.TestCase):
    def test_resolve_then_slugify_follows_redirect(self):
        resolved = provenance.ResolvedArticle(
            "Iran War", "List of wars involving Iran", 4242,
        )

        def fake_resolve(title):
            self.assertEqual(title, "Iran War")
            return resolved

        out = resolve_topic_for_action("Iran War", resolver=fake_resolve)
        self.assertEqual(out["requested_title"], "Iran War")
        self.assertEqual(out["canonical_title"], "List of wars involving Iran")
        self.assertEqual(out["slug"], "List_of_wars_involving_Iran")
        self.assertEqual(out["page_id"], 4242)
        self.assertTrue(out["redirected"])

    def test_url_normalize_then_resolve_then_slugify(self):
        resolved = provenance.ResolvedArticle(
            "Iran War", "List of wars involving Iran", 4242,
        )

        def fake_resolve(title):
            self.assertEqual(title, "Iran War")
            return resolved

        out = resolve_topic_for_action(
            "https://en.wikipedia.org/wiki/Iran_War",
            resolver=fake_resolve,
        )
        self.assertEqual(out["requested_title"], "Iran War")
        self.assertEqual(out["canonical_title"], "List of wars involving Iran")
        self.assertEqual(out["slug"], "List_of_wars_involving_Iran")
        self.assertTrue(out["redirected"])

    def test_no_redirect_not_flagged(self):
        resolved = provenance.ResolvedArticle("Zionism", "Zionism", 99)

        out = resolve_topic_for_action("Zionism", resolver=lambda _t: resolved)
        self.assertEqual(out["slug"], "Zionism")
        self.assertFalse(out["redirected"])

    def test_underscore_vs_space_not_redirect(self):
        # URL form vs display form of the same title should not count as a redirect.
        resolved = provenance.ResolvedArticle(
            "List of wars involving Iran",
            "List of wars involving Iran",
            1,
        )
        out = resolve_topic_for_action(
            "List_of_wars_involving_Iran",
            resolver=lambda _t: resolved,
            normalize=lambda value: value.replace("_", " ").strip(),
        )
        self.assertFalse(out["redirected"])
        self.assertEqual(out["slug"], "List_of_wars_involving_Iran")

    def test_empty_after_normalize_raises(self):
        with self.assertRaises(ValueError):
            resolve_topic_for_action("   ", resolver=lambda _t: None)


class TopicShardGateTests(unittest.TestCase):
    def test_gate_fails_hosted_without_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SHARD_DIR": tmp,
                "CORPUS_BACKEND": "hosted",
                "TOPIC": "Brand New Topic",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                code = topic_shard_gate.main([])
            self.assertEqual(code, 1)

    def test_gate_ok_ingest_without_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SHARD_DIR": tmp,
                "CORPUS_BACKEND": "ingest",
                "TOPIC": "Brand New Topic",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                code = topic_shard_gate.main([])
            self.assertEqual(code, 0)

    def test_gate_ok_hosted_with_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "provenance.duckdb").write_bytes(b"duck")
            env = {
                "SHARD_DIR": tmp,
                "CORPUS_BACKEND": "hosted",
                "TOPIC": "Zionism",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                code = topic_shard_gate.main([])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

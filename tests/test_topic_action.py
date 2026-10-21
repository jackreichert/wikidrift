"""Unit tests for Topic Action cold-start / delta corpus policy."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wikidrift.topic_action import plan_update_corpus


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

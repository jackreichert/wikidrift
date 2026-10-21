"""Unit tests for tools/seed_r2_shards.py helpers."""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest


def _load_seed_module():
    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / "tools" / "seed_r2_shards.py"
    spec = importlib.util.spec_from_file_location("seed_r2_shards", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


seed = _load_seed_module()


class SeedR2ShardsHelpersTest(unittest.TestCase):
    def test_normalize_prefix(self):
        self.assertEqual(seed._normalize_prefix(None), "")
        self.assertEqual(seed._normalize_prefix(""), "")
        self.assertEqual(seed._normalize_prefix("prod"), "prod/")
        self.assertEqual(seed._normalize_prefix("prod/"), "prod/")

    def test_is_lock_error(self):
        self.assertTrue(seed._is_lock_error(RuntimeError("Conflicting lock on database")))
        self.assertTrue(seed._is_lock_error(OSError("Could not set lock on file")))
        self.assertFalse(seed._is_lock_error(ValueError("missing table")))

    def test_discover_and_title_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = pathlib.Path(tmp) / "articles"
            shard = articles / "Brontosaurus"
            shard.mkdir(parents=True)
            (shard / "migration-manifest.json").write_text(
                '{"article": "Brontosaurus", "slug": "Brontosaurus"}\n',
                encoding="utf-8",
            )
            (articles / "_shared").mkdir()
            pairs = seed._resolve_selection(articles, topics=None, all_articles=True)
            self.assertEqual(pairs, [("Brontosaurus", "Brontosaurus")])

    def test_topics_selection_slugifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = pathlib.Path(tmp) / "articles"
            articles.mkdir()
            pairs = seed._resolve_selection(
                articles, topics=["Climate change"], all_articles=False
            )
            self.assertEqual(pairs, [("Climate_change", "Climate change")])


if __name__ == "__main__":
    unittest.main()

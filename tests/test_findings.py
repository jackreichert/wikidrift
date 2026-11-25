"""The findings-output layer: modules must persist viewer-shaped JSON into config.FINDINGS."""
import json
import pathlib
import tempfile
import unittest

from wikidrift import config
from wikidrift import l5_crosslingual as xl
from wikidrift.config import storage


class FindingsIO(unittest.TestCase):
    def setUp(self):
        self._orig_findings = storage.FINDINGS
        self._orig_articles = storage.ARTICLES_DIR
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        storage.FINDINGS = root / "findings"
        storage.ARTICLES_DIR = root / "articles"
        config.FINDINGS = storage.FINDINGS
        config.ARTICLES_DIR = storage.ARTICLES_DIR

    def tearDown(self):
        storage.FINDINGS = self._orig_findings
        storage.ARTICLES_DIR = self._orig_articles
        config.FINDINGS = self._orig_findings
        config.ARTICLES_DIR = self._orig_articles
        self._tmp.cleanup()

    def test_write_load_roundtrip(self):
        config.write_findings("x.json", {"a": 1, "b": [2, 3]})
        self.assertEqual(config.load_findings("x.json"), {"a": 1, "b": [2, 3]})

    def test_load_missing_returns_default(self):
        self.assertEqual(config.load_findings("nope.json"), {})
        self.assertEqual(config.load_findings("nope.json", {"static": {}}), {"static": {}})

    def test_write_mirrors_into_existing_article_shard(self):
        shard = storage.ARTICLES_DIR / "Abortion"
        shard.mkdir(parents=True)
        payload = {"article": "Abortion", "status": "confirmed", "run_ts": "2026-11-24T15:21:55Z"}
        config.write_findings("Abortion.l1-confirmation.json", payload)

        mirrored = shard / "findings" / "Abortion.l1-confirmation.json"
        self.assertTrue(mirrored.is_file())
        self.assertEqual(json.loads(mirrored.read_text(encoding="utf-8")), payload)
        self.assertEqual(config.load_findings("Abortion.l1-confirmation.json"), payload)

    def test_write_does_not_create_shard_when_absent(self):
        config.write_findings("Abortion.l1-confirmation.json", {"article": "Abortion"})
        self.assertFalse((storage.ARTICLES_DIR / "Abortion").exists())
        self.assertTrue((storage.FINDINGS / "Abortion.l1-confirmation.json").is_file())

    def test_write_skips_shared_artifacts_without_slug(self):
        storage.ARTICLES_DIR.mkdir(parents=True)
        config.write_findings("divergence.json", {"static": {}})
        self.assertTrue((storage.FINDINGS / "divergence.json").is_file())
        self.assertEqual(list(storage.ARTICLES_DIR.iterdir()), [])

    def test_emit_findings_writes_viewer_shapes(self):
        meta = {"en": {"present": True, "revid": 11, "title": "A",
                       "timestamp": "2020-01-01T00:00:00Z", "prose_chars": 100}}
        stat = {"variants": {"lead": {"divergence": 1.2, "spreads": {"Israel": 1.2}},
                             "focal": {"divergence": 1.0, "spreads": {}}},
                "editions": {"en": {"lead": {"Israel": {"stance": "neutral"}}, "focal": {}}}}
        pr = {"pivot": "2023-10-01", "read": "PEELED AWAY",
              "en_gap_before": 0.2, "en_gap_after": 0.7}
        xl.emit_findings("Demo Topic", "Q0", ["en"], ["Israel"], meta, stat, pr)

        rec = config.load_findings("Demo_Topic.receipts.json")
        self.assertEqual(rec["qid"], "Q0")
        self.assertEqual(rec["editions"]["en"]["revid"], 11)

        st = config.load_findings("Demo_Topic.stance.json")
        self.assertEqual(st["langs"], ["en"])
        self.assertEqual(st["editions"]["en"]["lead"]["Israel"]["stance"], "neutral")

        div = config.load_findings("divergence.json")
        self.assertEqual(div["static"]["Demo Topic"]["variants"]["lead"]["divergence"], 1.2)
        self.assertEqual(div["pivot_relative"]["Demo Topic"]["read"], "PEELED AWAY")


if __name__ == "__main__":
    unittest.main()

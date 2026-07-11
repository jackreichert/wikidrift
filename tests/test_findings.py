"""The findings-output layer: modules must persist viewer-shaped JSON into config.FINDINGS."""
import pathlib
import tempfile
import unittest

from wikidrift import config, l5_crosslingual as xl


class FindingsIO(unittest.TestCase):
    def setUp(self):
        self._orig = config.FINDINGS
        self._tmp = tempfile.TemporaryDirectory()
        config.FINDINGS = pathlib.Path(self._tmp.name)

    def tearDown(self):
        config.FINDINGS = self._orig
        self._tmp.cleanup()

    def test_write_load_roundtrip(self):
        config.write_findings("x.json", {"a": 1, "b": [2, 3]})
        self.assertEqual(config.load_findings("x.json"), {"a": 1, "b": [2, 3]})

    def test_load_missing_returns_default(self):
        self.assertEqual(config.load_findings("nope.json"), {})
        self.assertEqual(config.load_findings("nope.json", {"static": {}}), {"static": {}})

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

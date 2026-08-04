"""Golden-verdict regression for the L1 engine — the documented results must not silently drift.

Reads the cached DuckDB corpus read-only (no network). Skips gracefully if the corpus or a specific
article is absent, so the suite still runs on a fresh checkout without the ~350 MB DB.
"""
import unittest

import duckdb

from wikidrift import config, drift


@unittest.skipUnless(config.DB.exists(), "provenance.duckdb corpus not present")
class GoldenVerdicts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = duckdb.connect(str(config.DB), read_only=True)
        cls.present = {r[0] for r in cls.con.execute(
            "SELECT article FROM rsnap GROUP BY article HAVING count(distinct snap_rev) >= 3").fetchall()}

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def _label(self, article):
        if article not in self.present:
            self.skipTest(f"{article} not in corpus")
        return drift.candidate_verdict(self.con, article)[1]

    def test_zionism_is_the_canonical_pivot(self):
        label = self._label("Zionism")
        self.assertIn("PIVOT", label)
        self.assertIn("824,017", label)          # PWR-mass of the post-Oct-7 retrofit

    def test_photosynthesis_control_preserves_sub_legacy_floor_anomaly(self):
        label = self._label("Photosynthesis")
        self.assertIn("PIVOT?", label)
        self.assertIn("unconfirmed", label)

    def test_nakba_is_healthy_born_framed(self):
        # grown by addition, no removal-retrofit -> L1 correctly reads HEALTHY (L5 catches its framing)
        self.assertIn("HEALTHY", self._label("Nakba"))

    def test_naliboki_local_ingest_is_healthy(self):
        # also guards the local wikiwho_rs ingestion path end-to-end
        self.assertIn("HEALTHY", self._label("Naliboki massacre"))

    def test_water_pivot_is_old_and_not_demoted_by_age(self):
        # Water sits near the episode thresholds and can flip between tiny-PIVOT? and HEALTHY as the
        # cached corpus is refreshed. Guardrail: never classify it as sustained CREEP solely by age.
        label = self._label("Water")
        self.assertFalse(label.startswith("CREEP"), label)
        self.assertTrue(label.startswith("PIVOT") or label.startswith("HEALTHY"), label)


if __name__ == "__main__":
    unittest.main()

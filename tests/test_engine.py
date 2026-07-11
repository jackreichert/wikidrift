"""L1 engine on a SYNTHETIC in-memory corpus — so verdict_dict / episode detection / profile actually run
in CI without the (gitignored, ~850 MB) real DuckDB. Golden-verdict tests need the real corpus and auto-skip;
these don't — they build a tiny deterministic fixture and assert exact outputs.
"""
import os
import tempfile
import unittest
from unittest import mock

import duckdb

from wikidrift import provenance, drift, prerank


class EngineOnSyntheticCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Shrink the maturity floor so the fixture stays tiny (and the suite stays FAST) while exercising the
        # identical coarse→episode→verdict path. patch.object + addClassCleanup restores it even if setUp
        # raises after this point (a bare global assignment would leak the shrunk floor into other tests).
        patcher = mock.patch.object(drift, "MIN_MATURE", 10)
        patcher.start()
        cls.addClassCleanup(patcher.stop)

        fd, cls.path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(cls.path)                     # duckdb creates it fresh
        cls.con = duckdb.connect(cls.path)
        provenance.ensure_schema(cls.con)

        # --- SynthPivot: 4 snapshots; a 20-token spine (mature at floor=10) collapses to 5 at 2021→2022 ---
        # deterministic PIVOT?: interval loses 15 of 20 tokens, each weight 2 (present in the first 2 snaps)
        # ⇒ 75% persistence-weighted loss, PWR-mass destroyed = 15 × 2 = 30.
        spine = range(1, 21)
        survivors = range(1, 6)
        rows = []
        for rev, date, ids in [(1, "2020-01-01", spine), (2, "2021-01-01", spine),
                               (3, "2022-01-01", survivors), (4, "2023-01-01", survivors)]:
            rows += [("SynthPivot", date, rev, t, 1) for t in ids]
        cls.con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", rows)

        # --- TooFew: <3 snapshots ⇒ SKIP ---
        cls.con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)",
                            [("TooFew", "2020-01-01", 9, 1, 1), ("TooFew", "2021-01-01", 10, 1, 1)])

        # --- TinyProfile: one current snapshot, known origins ⇒ hand-checkable profile ---
        # 6 current tokens: 4 authored 2025-07 by EditorA (recent), 2 authored 2010 by EditorB (old).
        cls.con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)",
                            [("TinyProfile", "2026-01-01", 100, i, (200 if i <= 4 else 300)) for i in range(1, 7)])
        cls.con.executemany("INSERT INTO revisions VALUES (?,?,?,?)",
                            [("TinyProfile", 200, "2025-07-01T00:00:00Z", "EditorA"),
                             ("TinyProfile", 300, "2010-01-01T00:00:00Z", "EditorB")])
        provenance.ensure_indexes(cls.con)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        os.unlink(cls.path)

    def test_verdict_dict_detects_the_synthetic_pivot(self):
        v = drift.verdict_dict(self.con, "SynthPivot")
        self.assertEqual(v["verdict"], "PIVOT?")
        self.assertEqual(v["top_mass"], 30)                    # 15 tokens lost × persistence-weight 2
        e = v["episodes"][0]
        self.assertEqual((e["start"], e["end"]), ("2021-01-01", "2022-01-01"))
        self.assertGreaterEqual(e["peak_pct"], drift.MAG_FLOOR)  # 75% ≥ the pivot magnitude floor

    def test_verdict_dict_skips_with_too_few_snapshots(self):
        self.assertEqual(drift.verdict_dict(self.con, "TooFew")["verdict"], "SKIP")

    def test_coarse_returns_the_mature_interval_series(self):
        # coarse is now pure (no printing); the 2021→2022 interval loses 15 of 20 weight-2 tokens ⇒ 75%.
        snaps, members, present, _ = drift.load_membership(self.con, "SynthPivot")
        series, _stats = drift.coarse(snaps, members, present)
        self.assertTrue(any(abs(row[4] - 75.0) < 0.01 for row in series))

    def test_print_coarse_report_emits_the_interval_table(self):
        # the presentation half split out of coarse still prints the header + footer-stats lines.
        import io, contextlib
        snaps, members, present, _ = drift.load_membership(self.con, "SynthPivot")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift.print_coarse_report(snaps, members, present)
        out = buf.getvalue()
        self.assertIn("pwr_loss", out)
        self.assertIn("persistence-weighted loss:", out)

    def test_profile_recency_and_concentration(self):
        p = drift.profile(self.con, "TinyProfile")
        self.assertEqual(p["n_tokens"], 6)
        self.assertEqual(p["distinct_editors"], 2)
        self.assertEqual(p["top10_editor_share"], 100.0)        # 2 editors, all within the top 10
        self.assertAlmostEqual(p["pct_recent"], 66.7, delta=0.1)  # 4 of 6 authored within RECENT_YEARS
        self.assertLess(p["median_age_yrs"], 1.0)               # median token is the recent (2025) cohort


class PrerankRouting(unittest.TestCase):
    """The pre-rank router's three lead branches on synthetic metadata (revisions + rev_size), so each
    routing decision has a red-on-break test rather than only the corpus-dependent Orchestration path."""
    @classmethod
    def setUpClass(cls):
        cls.con = duckdb.connect(":memory:")
        provenance.ensure_schema(cls.con)
        cls._gen("SynthRemoval", cls._plateau(100000, 100300, 40) + [30000] * 24)   # big sustained drop
        cls._gen("SynthAddition", cls._plateau(30000, 30300, 40) + [120000] * 24)   # big sustained growth
        cls._gen("SynthChurn", cls._plateau(80000, 80200, 40) + [50000] * 24)       # medium (30k), very anomalous

    @classmethod
    def _plateau(cls, lo, hi, pairs):
        series = []
        for _ in range(pairs):
            series += [lo, hi]
        return series[:pairs]

    @classmethod
    def _gen(cls, article, sizes, step_days=10):
        import datetime as dt
        t0 = dt.date(2020, 1, 1)
        rows_r, rows_z = [], []
        for i, s in enumerate(sizes):
            ts = (t0 + dt.timedelta(days=i * step_days)).isoformat() + "T00:00:00Z"
            rows_r.append((article, i + 1, ts, f"ed{i % 3}"))
            rows_z.append((article, i + 1, s))
        cls.con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", rows_r)
        cls.con.executemany("INSERT INTO rev_size VALUES (?,?,?)", rows_z)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_large_sustained_removal_routes_to_the_pwr_engine(self):
        leads = prerank.prerank(self.con, "SynthRemoval")["leads"]
        self.assertIn("removal→PWR", leads)

    def test_large_sustained_growth_routes_to_l2(self):
        leads = prerank.prerank(self.con, "SynthAddition")["leads"]
        self.assertIn("addition→L2", leads)

    def test_medium_but_highly_anomalous_removal_routes_to_l2_as_churn(self):
        # 30k removed is below the absolute LEAD_FLOOR (so removal→PWR must NOT fire) but ≫ the article's
        # own baseline ⇒ reframe-by-churn, handed to L2 (the Palestinian-political-violence gap).
        leads = prerank.prerank(self.con, "SynthChurn")["leads"]
        self.assertIn("churn→L2", leads)
        self.assertNotIn("removal→PWR", leads)


class DestroyerAttribution(unittest.TestCase):
    """drift.destroyers — WHO removed the established spine — with WikiWho (tokens_at) mocked at the boundary."""
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        provenance.ensure_schema(self.con)
        self.con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", [
            ("A", 100, "2019-01-01T00:00:00Z", "OldAuthor"),   # token origin — established BEFORE the window
            ("A", 550, "2021-03-01T00:00:00Z", "Destroyer"),   # terminal 'out' rev — INSIDE the window
        ])
        # latest snapshot holds only token 2 (a survivor), so token 1's removal counts as destruction.
        self.con.execute("INSERT INTO rsnap VALUES (?,?,?,?,?)", ("A", "2021-07-01", 600, 2, 100))
        self.addCleanup(self.con.close)

    def test_attributes_removed_established_spine_to_the_deleting_editor(self):
        canned = [
            {"token_id": 1, "o_rev_id": 100, "out": [550]},   # established, killed in-window, gone from cur → KILLED
            {"token_id": 2, "o_rev_id": 100, "out": [550]},   # same but survives (still in latest snapshot) → skip
            {"token_id": 3, "o_rev_id": 100, "out": []},      # never removed → skip
            {"token_id": 4, "o_rev_id": 999, "out": [550]},   # unknown origin → not established → skip
        ]
        peak = ("2021-01-01", 500, "2021-07-01", 600, 50.0)
        with mock.patch.object(provenance, "tokens_at", lambda art, rev, io=False: canned):
            killers, killed, origin_ts, editor_of, latest = drift.destroyers("A", con=self.con, peak=peak)
        self.assertEqual(killed, 1)
        self.assertEqual(killers, {"Destroyer": 1})
        # the refactor contract: destroyers now returns the revision maps + latest row so attribute reuses them.
        self.assertEqual(latest, (600,))
        self.assertEqual(editor_of[550], "Destroyer")


class RefineBinarySearch(unittest.TestCase):
    """drift.refine — the binary-search confirmation that the durable spine actually collapsed, and WHERE.
    WikiWho (tokens_at) is mocked at the boundary; MIN_COHORT is shrunk so the fixture stays tiny."""
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        provenance.ensure_schema(self.con)
        self.con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", [
            ("A", 5,  "2020-06-01T00:00:00Z", "before"),   # before the window — must be excluded
            ("A", 11, "2021-02-01T00:00:00Z", "u"),
            ("A", 12, "2021-03-01T00:00:00Z", "u"),
            ("A", 13, "2021-04-01T00:00:00Z", "u"),
            ("A", 14, "2021-05-01T00:00:00Z", "Destroyer"),
            ("A", 15, "2021-06-01T00:00:00Z", "u"),
            ("A", 30, "2022-01-01T00:00:00Z", "after"),    # after the window — must be excluded
        ])
        self.addCleanup(self.con.close)
        # 8-token durable spine at the interval-start snapshot (index 0); equal weights ⇒ the whole set is the cohort.
        self.members = [set(range(1, 9))]
        self.present = {t: [0] for t in range(1, 9)}
        self.idx_of_rev = {10: 0}
        self.peak = ("2021-01-01", 10, "2021-07-01", 20, 50.0)

    def _run(self, toksets):
        def fake_tokens_at(art, rev, io=False):
            return [{"token_id": t} for t in toksets[rev]]
        with mock.patch.object(provenance, "tokens_at", fake_tokens_at), \
             mock.patch.object(drift, "MIN_COHORT", 3):
            return drift.refine("A", self.con, [], self.members, self.present, self.idx_of_rev, self.peak)

    def test_binary_search_locates_the_durable_spine_collapse(self):
        # spine intact through rev 13, collapses to 2 of 8 at rev 14 → the confirmed drop is between 13 and 14.
        lo, hi, drop = self._run({11: set(range(1, 9)), 12: set(range(1, 9)), 13: set(range(1, 9)),
                                  14: {1, 2}, 15: {1, 2}})
        self.assertEqual((lo[0], hi[0]), (13, 14))
        self.assertAlmostEqual(drop, 0.75)
        self.assertGreaterEqual(drop, drift.CONFIRM_DROP)     # ≥ threshold ⇒ a confirmed pivot

    def test_stable_spine_is_not_confirmed(self):
        # the durable spine survives the whole window ⇒ interval_drop ≈ 0, below CONFIRM_DROP: a change that
        # did NOT destroy the established spine is not a confirmed pivot (the false-positive guard).
        _, _, drop = self._run({r: set(range(1, 9)) for r in (11, 12, 13, 14, 15)})
        self.assertAlmostEqual(drop, 0.0)
        self.assertLess(drop, drift.CONFIRM_DROP)

    def test_too_few_in_window_revisions_returns_none(self):
        # <3 revisions inside the window can't be binary-searched ⇒ None (guard fires before any WikiWho call).
        self.con.execute("DELETE FROM revisions WHERE rev_id IN (12, 13, 14, 15)")   # leaves only rev 11 in-window
        self.assertIsNone(self._run({11: set(range(1, 9))}))


if __name__ == "__main__":
    unittest.main()

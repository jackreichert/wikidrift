"""L1 engine on a SYNTHETIC in-memory corpus — so verdict_dict / episode detection / profile actually run
in CI without the (gitignored, ~850 MB) real DuckDB. Golden-verdict tests need the real corpus and auto-skip;
these don't — they build a tiny deterministic fixture and assert exact outputs.
"""
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import duckdb

from wikidrift import benchmark, config, provenance, drift, prerank


class ArticleTitleResolution(unittest.TestCase):
    def test_resolves_redirect_to_canonical_title_and_page_id(self):
        response = {
            "query": {
                "redirects": [{
                    "from": "Democratic Party of the United States",
                    "to": "Democratic Party (United States)",
                }],
                "pages": [{
                    "pageid": 5043544,
                    "title": "Democratic Party (United States)",
                }],
            }
        }

        with mock.patch.object(provenance.config, "get_json_retrying", return_value=response) as get_json:
            resolved = provenance.resolve_article_title("Democratic Party of the United States")

        self.assertEqual(resolved.requested_title, "Democratic Party of the United States")
        self.assertEqual(resolved.canonical_title, "Democratic Party (United States)")
        self.assertEqual(resolved.page_id, 5043544)
        self.assertEqual(get_json.call_args.kwargs["params"]["redirects"], 1)

    def test_rejects_missing_article(self):
        response = {"query": {"pages": [{"ns": 0, "title": "Missing", "missing": True}]}}

        with mock.patch.object(provenance.config, "get_json_retrying", return_value=response):
            with self.assertRaisesRegex(ValueError, "article not found"):
                provenance.resolve_article_title("Missing")

    def test_ensure_sizes_initializes_empty_database_with_canonical_identity(self):
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        resolved = provenance.ResolvedArticle("Testland", "Testland", 123)
        history = {
            "query": {"pages": [{"pageid": 123, "title": "Testland", "revisions": [{
                "revid": 10,
                "timestamp": "2026-01-01T00:00:00Z",
                "user": "Editor",
                "size": 100,
            }]}]}
        }

        with mock.patch.object(provenance, "resolve_article_title", return_value=resolved), \
             mock.patch.object(provenance.config, "get_json_retrying", return_value=history):
            provenance.ensure_sizes(con, "Testland")

        self.assertEqual(con.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)
        self.assertEqual(
            con.execute("SELECT requested_title, canonical_title, page_id FROM article_identity").fetchone(),
            ("Testland", "Testland", 123),
        )


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
        # ⇒ 75% persistence-weighted loss, PWR-mass removed = 15 × 2 = 30.
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


class RemovalAttribution(unittest.TestCase):
    """Attribute established-token removals with WikiWho (tokens_at) mocked at the boundary."""
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        provenance.ensure_schema(self.con)
        self.con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", [
            ("A", 100, "2019-01-01T00:00:00Z", "OldAuthor"),   # token origin — established BEFORE the window
            ("A", 550, "2021-03-01T00:06:00Z", "RemovingEditor"),  # terminal 'out' rev inside the window
        ])
        # Latest snapshot holds only token 2, so token 1 counts as persistently removed.
        self.con.execute("INSERT INTO rsnap VALUES (?,?,?,?,?)", ("A", "2021-07-01", 600, 2, 100))
        self.addCleanup(self.con.close)

    def test_attributes_removed_established_spine_to_the_deleting_editor(self):
        canned = [
            {"token_id": 1, "o_rev_id": 100, "out": [550]},   # established, removed in-window, still absent
            {"token_id": 2, "o_rev_id": 100, "out": [550]},   # same but survives (still in latest snapshot) → skip
            {"token_id": 3, "o_rev_id": 100, "out": []},      # never removed → skip
            {"token_id": 4, "o_rev_id": 999, "out": [550]},   # unknown origin → not established → skip
        ]
        peak = ("2021-01-01", 500, "2021-07-01", 600, 50.0)
        with mock.patch.object(provenance, "tokens_at", lambda art, rev, io=False: canned):
            removals_by_editor, removed_count, origin_ts, editor_of, latest = drift.removal_attribution(
                "A", con=self.con, peak=peak
            )
        self.assertEqual(removed_count, 1)
        self.assertEqual(removals_by_editor, {"RemovingEditor": 1})
        # The return contract includes revision maps + latest row so attribute can reuse them.
        self.assertEqual(latest, (600,))
        self.assertEqual(editor_of[550], "RemovingEditor")

    def test_exact_event_attribution_returns_recomputable_counts_and_duration(self):
        self.con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", [
            ("A", 500, "2021-03-01T00:00:00Z", "BeforeEditor"),
            ("A", 560, "2021-03-01T00:12:00Z", "ReplacementEditor"),
            ("A", 600, "2021-03-01T00:18:00Z", "AfterEditor"),
        ])
        episode = {
            "before_revid": 500,
            "before_timestamp": "2021-03-01T00:00:00Z",
            "after_revid": 600,
            "after_timestamp": "2021-03-01T00:18:00Z",
        }
        before = [
            {"token_id": 1, "o_rev_id": 100, "out": [550]},
            {"token_id": 2, "o_rev_id": 100, "out": []},
        ]
        after = [
            {"token_id": 2, "o_rev_id": 100},
            {"token_id": 3, "o_rev_id": 560},
        ]
        with mock.patch.object(provenance, "tokens_at", side_effect=[before, after]):
            result = drift.event_attribution("A", self.con, episode)

        self.assertEqual(result["removed_tokens"], 1)
        self.assertEqual(result["replacement_tokens"], 1)
        self.assertEqual(result["removals_by_editor"], [
            {"editor": "RemovingEditor", "tokens": 1},
        ])
        self.assertEqual(result["replacement_by_editor"], [
            {"editor": "ReplacementEditor", "tokens": 1},
        ])
        self.assertEqual(result["duration_seconds"], 1080)
        self.assertEqual(result["top_removal_share"], 1.0)
        self.assertEqual(result["top_replacement_share"], 1.0)
        self.assertFalse(result["same_top_editor"])


class AttributionBackfill(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        provenance.ensure_schema(self.con)
        self.con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", [
            ("A", "2025-01-01", 900, 1, 100),
            ("A", "2025-01-01", 900, 2, 100),
        ])
        self.confirmation = {
            "article": "A",
            "status": "confirmed",
            "thresholds": config.confirmation_thresholds(),
            "corpus_horizon": {"snapshot_date": "2025-01-01", "snapshot_revid": 900},
            "confirmed_episodes": [{
                "before_revid": 500,
                "before_timestamp": "2024-12-01T00:00:00Z",
                "after_revid": 600,
                "after_timestamp": "2024-12-01T00:18:00Z",
            }],
        }
        self.addCleanup(self.con.close)

    def test_backfills_missing_attribution_and_persists_confirmation(self):
        attribution = {"duration_seconds": 1080, "removed_tokens": 2}
        with mock.patch.object(drift, "load_confirmation", return_value=self.confirmation), \
             mock.patch.object(drift, "event_attribution", return_value=attribution), \
             mock.patch.object(drift.config, "write_findings") as write_findings:
            report = drift.backfill_attribution("A", con=self.con)

        episode = self.confirmation["confirmed_episodes"][0]
        self.assertEqual(episode["attribution"], attribution)
        self.assertEqual(episode["duration_seconds"], 1080)
        self.assertEqual(report["updated_episodes"], 1)
        self.assertEqual(report["skipped_episodes"], 0)
        write_findings.assert_called_once_with("A.l1-confirmation.json", self.confirmation)

    def test_complete_episode_is_skipped_idempotently(self):
        self.confirmation["confirmed_episodes"][0]["attribution"] = {
            "duration_seconds": 1080,
        }
        with mock.patch.object(drift, "load_confirmation", return_value=self.confirmation), \
             mock.patch.object(drift, "event_attribution") as event_attribution, \
             mock.patch.object(drift.config, "write_findings") as write_findings:
            report = drift.backfill_attribution("A", con=self.con)

        self.assertEqual(report["updated_episodes"], 0)
        self.assertEqual(report["skipped_episodes"], 1)
        event_attribution.assert_not_called()
        write_findings.assert_not_called()

    def test_stale_confirmation_is_rejected_before_token_fetch(self):
        self.confirmation["corpus_horizon"]["snapshot_revid"] = 899
        with mock.patch.object(drift, "load_confirmation", return_value=self.confirmation), \
             mock.patch.object(drift, "event_attribution") as event_attribution:
            with self.assertRaisesRegex(ValueError, "corpus horizon"):
                drift.backfill_attribution("A", con=self.con)

        event_attribution.assert_not_called()


class ConcentrationCalibrationReport(unittest.TestCase):
    def test_reads_fresh_article_owned_shard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            article_dir = pathlib.Path(temp_dir) / "Example"
            findings_dir = article_dir / "findings"
            findings_dir.mkdir(parents=True)
            database = article_dir / "provenance.duckdb"
            con = duckdb.connect(str(database))
            provenance.ensure_schema(con)
            con.execute("INSERT INTO rsnap VALUES (?,?,?,?,?)", ("Example", "2026-01-01", 900, 1, 100))
            con.close()
            confirmation = {
                "article": "Example",
                "status": "confirmed",
                "thresholds": config.confirmation_thresholds(),
                "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": 900},
                "confirmed_episodes": [{
                    "before_revid": 100,
                    "after_revid": 200,
                    "durable_spine_drop": 0.5,
                    "pwr_mass": 100000,
                    "attribution": {
                        "duration_seconds": 60,
                        "removed_tokens": 2,
                        "replacement_tokens": 1,
                        "removals_by_editor": [{"editor": "Editor A", "tokens": 2}],
                        "replacement_by_editor": [{"editor": "Editor B", "tokens": 1}],
                    },
                }],
            }
            artifact = findings_dir / "Example.l1-confirmation.json"
            artifact.write_text(json.dumps(confirmation), encoding="utf-8")

            report = benchmark.concentration_report(temp_dir)

        self.assertEqual(len(report["events"]), 1)
        self.assertEqual(report["exclusions"], [])
        self.assertEqual(report["summary"]["event_count"], 1)
        self.assertFalse(report["calibration_ready"])
        self.assertFalse(report["labels_enabled"])


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
            ("A", 14, "2021-05-01T00:00:00Z", "RemovingEditor"),
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


class AnalyzeConfirmationContract(unittest.TestCase):
    def test_partial_snapshot_coverage_returns_unavailable_before_analysis(self):
        con = duckdb.connect(":memory:")
        provenance.ensure_schema(con)
        self.addCleanup(con.close)
        source_state = {
            "article": "A",
            "source_status": "partial",
            "expected_snapshots": 25,
            "loaded_snapshots": 24,
            "reason": "loaded 24 of 25 expected snapshots",
        }

        with mock.patch.object(provenance, "ensure_sizes"), \
             mock.patch.object(provenance, "ensure_indexes"), \
             mock.patch.object(provenance, "build_snapshots"), \
             mock.patch.object(provenance, "load_source_state", return_value=source_state), \
             mock.patch.object(drift, "ranked_episodes") as ranked, \
             mock.patch.object(drift.config, "write_findings") as write_findings:
            result = drift.analyze("A", con=con)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], source_state["reason"])
        self.assertEqual(result["source_state"], source_state)
        ranked.assert_not_called()
        write_findings.assert_called_once_with("A.l1-confirmation.json", result)

    def test_returns_and_persists_exact_confirmed_pair(self):
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        episode = {
            "start": ("2020-01-01", 10), "end": ("2021-01-01", 20),
            "abs": 42000, "peak": 40.0, "age": 2.0,
        }
        ranked = (
            [("2020-01-01", 10), ("2021-01-01", 20), ("2024-01-01", 900)],
            [set(), set(), set()], {}, {}, [], (4.0, 2.0, 1.0), [episode],
        )
        confirmation = ((111, "2020-06-01T00:00:00Z", "Before"),
                        (112, "2020-06-02T00:00:00Z", "After"), 0.4)
        attribution = {
            "duration_seconds": 86400,
            "removed_tokens": 10,
            "replacement_tokens": 8,
            "removals_by_editor": [{"editor": "Editor A", "tokens": 10}],
            "replacement_by_editor": [{"editor": "Editor B", "tokens": 8}],
            "top_removal_share": 1.0,
            "top_replacement_share": 1.0,
            "same_top_editor": False,
            "top_two_removal_share": 1.0,
        }

        with mock.patch.object(provenance, "ensure_sizes"), \
             mock.patch.object(provenance, "ensure_indexes"), \
             mock.patch.object(provenance, "build_snapshots"), \
             mock.patch.object(drift, "ranked_episodes", return_value=ranked), \
             mock.patch.object(drift, "verdict_dict", return_value={"verdict": "PIVOT?"}), \
             mock.patch.object(drift, "refine", return_value=confirmation), \
             mock.patch.object(drift, "attribute", return_value=attribution), \
             mock.patch.object(drift, "print_coarse_report"), \
             mock.patch.object(drift.config, "write_findings") as write_findings:
            result = drift.analyze("A", con=con)

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["corpus_horizon"], {
            "snapshot_date": "2024-01-01", "snapshot_revid": 900,
        })
        confirmed = result["confirmed_episodes"][0]
        self.assertEqual((confirmed["before_revid"], confirmed["after_revid"]), (111, 112))
        self.assertEqual(confirmed["durable_spine_drop"], 0.4)
        self.assertEqual(confirmed["duration_seconds"], 86400)
        self.assertEqual(confirmed["attribution"], attribution)
        write_findings.assert_called_once_with("A.l1-confirmation.json", result)

    def test_rolling_second_pass_runs_when_primary_candidates_do_not_confirm(self):
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        primary = {
            "start": ("2021-01-01", 10), "end": ("2022-01-01", 20),
            "abs": 100000, "peak": 40.0, "age": 4.0,
        }
        rolling = {
            "start": ("2023-01-01", 30), "end": ("2024-01-01", 40),
            "abs": 90000, "peak": 24.0, "age": 2.0, "source": "rolling",
        }
        ranked = (
            [("2021-01-01", 10), ("2022-01-01", 20), ("2023-01-01", 30),
             ("2024-01-01", 40), ("2026-01-01", 900)],
            [set(), set(), set(), set(), set()], {}, {}, [], (9.0, 2.0, 1.0), [primary],
        )
        primary_result = ((111, "2021-06-01T00:00:00Z", "Before"),
                          (112, "2021-06-02T00:00:00Z", "After"), 0.1)
        rolling_result = ((211, "2023-06-01T00:00:00Z", "Before"),
                          (212, "2023-06-02T00:00:00Z", "After"), 0.3)

        with mock.patch.object(provenance, "ensure_sizes"), \
             mock.patch.object(provenance, "ensure_indexes"), \
             mock.patch.object(provenance, "build_snapshots"), \
             mock.patch.object(drift, "ranked_episodes", return_value=ranked), \
             mock.patch.object(drift, "rolling_candidates", return_value=[rolling]), \
             mock.patch.object(drift, "verdict_dict", return_value={"verdict": "PIVOT?"}), \
             mock.patch.object(drift, "refine", side_effect=[primary_result, rolling_result]), \
             mock.patch.object(drift, "attribute"), \
             mock.patch.object(drift, "print_coarse_report"), \
             mock.patch.object(drift.config, "write_findings"):
            result = drift.analyze("A", con=con)

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["confirmed_episodes"][0]["source"], "rolling")
        self.assertEqual(result["confirmed_episodes"][0]["before_revid"], 211)


class SourceCoverageState(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        provenance.ensure_schema(self.con)
        self.addCleanup(self.con.close)

    def test_load_source_state_degrades_when_legacy_table_is_absent(self):
        legacy = duckdb.connect(":memory:")
        self.addCleanup(legacy.close)
        self.assertIsNone(provenance.load_source_state(legacy, "A"))

    def test_record_source_state_is_idempotent_without_primary_key(self):
        migrated = duckdb.connect(":memory:")
        self.addCleanup(migrated.close)
        migrated.execute("""CREATE TABLE article_source_state(
            article TEXT, source_status TEXT, source_checked_at TEXT,
            source_latest_revid BIGINT, expected_snapshots INT,
            loaded_snapshots INT, reason TEXT)""")

        provenance.record_source_state(migrated, "A", source_status="partial")
        provenance.record_source_state(migrated, "A", source_status="current_complete")

        self.assertEqual(
            migrated.execute(
                "SELECT count(*) FROM article_source_state WHERE article='A'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            provenance.load_source_state(migrated, "A")["source_status"],
            "current_complete",
        )

    def test_record_source_state_updates_without_losing_history_metadata(self):
        provenance.record_source_state(
            self.con,
            "A",
            source_status="history_complete",
            source_checked_at="2026-07-30T00:00:00+00:00",
            source_latest_revid=900,
        )
        provenance.record_source_state(
            self.con,
            "A",
            source_status="partial",
            expected_snapshots=2,
            loaded_snapshots=1,
            reason="loaded 1 of 2 expected snapshots",
        )

        state = provenance.load_source_state(self.con, "A")
        self.assertEqual(state["source_latest_revid"], 900)
        self.assertEqual(state["expected_snapshots"], 2)
        self.assertEqual(state["loaded_snapshots"], 1)
        self.assertEqual(state["source_status"], "partial")

        provenance.record_source_state(
            self.con,
            "A",
            source_status="current_complete",
            loaded_snapshots=2,
            reason=None,
        )
        refreshed = provenance.load_source_state(self.con, "A")
        self.assertEqual(refreshed["source_latest_revid"], 900)
        self.assertEqual(refreshed["source_status"], "current_complete")
        self.assertIsNone(refreshed["reason"])

    def test_build_snapshots_persists_partial_coverage(self):
        picks = [("2025-01-01", 100), ("2026-01-01", 200)]
        token_results = [[{"token_id": 1, "o_rev_id": 10}], []]
        with mock.patch.object(provenance, "snapshot_picks", return_value=picks), \
             mock.patch.object(provenance, "tokens_at", side_effect=token_results), \
             mock.patch.object(provenance.time, "sleep"):
            snapshots = provenance.build_snapshots(self.con, "A")

        self.assertEqual(snapshots, [("2025-01-01", 100)])
        state = provenance.load_source_state(self.con, "A")
        self.assertEqual(state["source_status"], "partial")
        self.assertEqual((state["expected_snapshots"], state["loaded_snapshots"]), (2, 1))
        self.assertIn("1 of 2", state["reason"])

    def test_retry_exhaustion_persists_unavailable_source(self):
        resolved = provenance.ResolvedArticle("A", "A", 1)
        with mock.patch.object(provenance, "resolve_article_title", return_value=resolved), \
             mock.patch.object(
                 provenance.config,
                 "get_json_retrying",
                 side_effect=TimeoutError("Action API timed out"),
             ):
            with self.assertRaises(TimeoutError):
                provenance.ensure_sizes(self.con, "A")

        state = provenance.load_source_state(self.con, "A")
        self.assertEqual(state["source_status"], "unavailable")
        self.assertIn("history retrieval failed", state["reason"])
        self.assertIn("Action API timed out", state["reason"])


if __name__ == "__main__":
    unittest.main()

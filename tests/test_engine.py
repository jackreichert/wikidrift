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

from wikidrift import benchmark, cli, config, event_attribution, provenance, drift, l4, prerank, trust
from wikidrift.corpus import Corpus


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
                "parentid": 9,
                "sha1": "abc123",
                "comment": "/* History */ clarify chronology",
                "tags": ["visualeditor"],
                "minor": True,
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
        self.assertEqual(
            con.execute("""SELECT parent_id, sha1, comment, tags, minor
                FROM revision_metadata WHERE article='Testland' AND rev_id=10""").fetchone(),
            (9, "abc123", "/* History */ clarify chronology", '["visualeditor"]', True),
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
        token_states = {
            500: before,
            550: [{"token_id": 2, "o_rev_id": 100}],
            560: after,
            600: after,
        }
        with mock.patch.object(
                provenance, "tokens_at", side_effect=lambda article, revision, io=False: token_states[revision]):
            result = drift.event_attribution("A", self.con, episode)

        self.assertEqual(result["schema_version"], 3)
        self.assertEqual([row["revision_id"] for row in result["revisions"]], [500, 550, 560, 600])
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


class MultiRevisionEventAttribution(unittest.TestCase):
    def test_reverted_activity_stays_gross_while_surviving_work_is_distributed(self):
        revisions = [
            {"revision_id": 100, "timestamp": "2021-01-01T00:00:00Z", "account": "Before",
             "tokens": [{"token_id": 1, "o_rev_id": 50}, {"token_id": 2, "o_rev_id": 50}]},
            {"revision_id": 110, "timestamp": "2021-01-01T00:05:00Z", "account": "Alice",
             "tokens": [{"token_id": 2, "o_rev_id": 50}, {"token_id": 3, "o_rev_id": 110}]},
            {"revision_id": 120, "timestamp": "2021-01-01T00:10:00Z", "account": "Bob",
             "tokens": [{"token_id": 2, "o_rev_id": 50}, {"token_id": 3, "o_rev_id": 110},
                        {"token_id": 4, "o_rev_id": 120}]},
            {"revision_id": 130, "timestamp": "2021-01-01T00:15:00Z", "account": "Carol",
             "tokens": [{"token_id": 1, "o_rev_id": 50}, {"token_id": 2, "o_rev_id": 50},
                        {"token_id": 3, "o_rev_id": 110}, {"token_id": 4, "o_rev_id": 120},
                        {"token_id": 5, "o_rev_id": 130}]},
            {"revision_id": 140, "timestamp": "2021-01-01T00:20:00Z", "account": "Dave",
             "tokens": [{"token_id": 2, "o_rev_id": 50}, {"token_id": 3, "o_rev_id": 110},
                        {"token_id": 4, "o_rev_id": 120}, {"token_id": 5, "o_rev_id": 130}]},
        ]

        result = event_attribution.attribute_revision_sequence("Example", revisions)

        self.assertEqual(result["schema_version"], 3)
        self.assertEqual(result["gross"], {
            "removed_tokens": 2, "added_tokens": 4, "restored_tokens": 1,
        })
        self.assertEqual(result["net_standing"], {
            "removed_tokens": 1, "replacement_tokens": 3,
        })
        self.assertEqual(result["removals_by_editor"], [{"editor": "Dave", "tokens": 1}])
        self.assertEqual(result["replacement_by_editor"], [
            {"editor": "Alice", "tokens": 1},
            {"editor": "Bob", "tokens": 1},
            {"editor": "Carol", "tokens": 1},
        ])
        self.assertAlmostEqual(result["participation"]["top_replacement_share"], 1 / 3, places=6)
        self.assertEqual(result["revisions"][1]["role"], "initiating_change")
        self.assertEqual(result["revisions"][3]["role"], "restoration")
        self.assertEqual(result["revisions"][3]["restores_revision_id"], 100)
        self.assertEqual(result["revisions"][4]["role"], "consolidation")
        self.assertEqual(result["revisions"][1]["gross_removed_tokens"], 1)
        self.assertEqual(result["revisions"][1]["standing_removed_tokens"], 0)
        self.assertEqual(result["revisions"][4]["standing_removed_tokens"], 1)

    def test_account_states_remain_distinct_without_identity_inference(self):
        cases = [
            ({}, "hidden"),
            ({"account": "192.0.2.1"}, "anonymous_ip"),
            ({"account": "ExampleBot"}, "bot"),
            ({"account": "Renamed account", "account_type": "renamed"}, "renamed"),
            ({"account": "<unavailable>", "account_type": "unavailable"}, "unavailable"),
        ]
        for account_fields, expected in cases:
            with self.subTest(account_type=expected):
                revisions = [
                    {"revision_id": 1, "timestamp": "2025-01-01T00:00:00Z", "account": "Before",
                     "tokens": [{"token_id": 1, "o_rev_id": 1}]},
                    {"revision_id": 2, "timestamp": "2025-01-01T00:01:00Z", **account_fields,
                     "tokens": [{"token_id": 2, "o_rev_id": 2}]},
                ]
                result = event_attribution.attribute_revision_sequence("Example", revisions)
                self.assertEqual(result["revisions"][1]["account_type"], expected)

    def test_fully_restored_sequence_preserves_gross_activity_as_reverted(self):
        revisions = [
            {"revision_id": 1, "timestamp": "2025-01-01T00:00:00Z", "account": "Before",
             "tokens": [{"token_id": 1, "o_rev_id": 1}]},
            {"revision_id": 2, "timestamp": "2025-01-01T00:01:00Z", "account": "Changer",
             "tokens": [{"token_id": 2, "o_rev_id": 2}]},
            {"revision_id": 3, "timestamp": "2025-01-01T00:02:00Z", "account": "Restorer",
             "tokens": [{"token_id": 1, "o_rev_id": 1}]},
        ]

        result = event_attribution.attribute_revision_sequence("Example", revisions)

        self.assertEqual(result["event_status"], "reverted")
        self.assertEqual(result["gross"]["removed_tokens"], 2)
        self.assertEqual(result["net_standing"], {"removed_tokens": 0, "replacement_tokens": 0})
        self.assertEqual(result["revisions"][-1]["role"], "revert")
        self.assertEqual(result["revisions"][-1]["restores_revision_id"], 1)


class RevisionEvidenceCompatibility(unittest.TestCase):
    def test_legacy_shard_without_metadata_returns_timeline_evidence(self):
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("CREATE TABLE revisions(article TEXT, rev_id BIGINT, ts TEXT, user TEXT)")
        con.execute("INSERT INTO revisions VALUES ('A', 1, '2025-01-01T00:00:00Z', 'Editor')")

        rows = Corpus(con).revision_evidence_between(
            "A", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"
        )

        self.assertEqual(rows, [{
            "revision_id": 1, "timestamp": "2025-01-01T00:00:00Z", "account": "Editor",
        }])

    def test_non_catalog_database_errors_are_not_hidden_as_legacy_schema(self):
        con = mock.Mock()
        con.execute.side_effect = duckdb.InternalException("database failure")

        with self.assertRaisesRegex(duckdb.InternalException, "database failure"):
            Corpus(con).revision_evidence_between("A", "start", "end")


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
        self.assertEqual(self.confirmation["schema_version"], drift.CONFIRMATION_SCHEMA_VERSION)
        self.assertEqual(report["updated_episodes"], 1)
        self.assertEqual(report["skipped_episodes"], 0)
        write_findings.assert_called_once_with("A.l1-confirmation.json", self.confirmation)

    def test_backfills_process_context_and_persists_confirmation(self):
        receipt = {"schema_version": 1, "semantic_role": "descriptive_process_context"}
        with mock.patch.object(drift, "load_confirmation", return_value=self.confirmation), \
             mock.patch.object(
                 drift.process_context, "retrieve_process_context", return_value=receipt
             ) as retrieve, \
             mock.patch.object(drift.config, "write_findings") as write_findings:
            report = drift.backfill_process_context("A", con=self.con)

        episode = self.confirmation["confirmed_episodes"][0]
        self.assertEqual(episode["process_context"], receipt)
        self.assertEqual(report["updated_episodes"], 1)
        retrieve.assert_called_once_with("A", episode)
        write_findings.assert_called_once_with("A.l1-confirmation.json", self.confirmation)

    def test_complete_legacy_episode_is_upgraded_without_recomputation(self):
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
        write_findings.assert_called_once_with("A.l1-confirmation.json", self.confirmation)
        self.assertEqual(self.confirmation["schema_version"], drift.CONFIRMATION_SCHEMA_VERSION)

    def test_complete_versioned_episode_is_skipped_idempotently(self):
        self.confirmation["schema_version"] = drift.CONFIRMATION_SCHEMA_VERSION
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

    def test_future_schema_is_rejected_without_downgrade(self):
        future_version = drift.CONFIRMATION_SCHEMA_VERSION + 1
        self.confirmation["schema_version"] = future_version
        with mock.patch.object(drift, "load_confirmation", return_value=self.confirmation), \
             mock.patch.object(drift.config, "write_findings") as write_findings:
            with self.assertRaisesRegex(ValueError, "newer than supported"):
                drift.backfill_attribution("A", con=self.con)

        self.assertEqual(self.confirmation["schema_version"], future_version)
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


class ConfirmedEventGraphReport(unittest.TestCase):
    def test_excludes_invalid_artifacts_and_unavailable_corpora(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_dir = pathlib.Path(temp_dir) / "Invalid" / "findings"
            invalid_dir.mkdir(parents=True)
            (invalid_dir / "Invalid.l1-confirmation.json").write_text("{", encoding="utf-8")

            missing_dir = pathlib.Path(temp_dir) / "Missing" / "findings"
            missing_dir.mkdir(parents=True)
            (missing_dir / "Missing.l1-confirmation.json").write_text(
                json.dumps({"article": "Missing", "status": "confirmed"}), encoding="utf-8",
            )

            corrupt_dir = pathlib.Path(temp_dir) / "Corrupt"
            corrupt_findings = corrupt_dir / "findings"
            corrupt_findings.mkdir(parents=True)
            (corrupt_findings / "Corrupt.l1-confirmation.json").write_text(
                json.dumps({"article": "Corrupt", "status": "confirmed"}), encoding="utf-8",
            )
            (corrupt_dir / "provenance.duckdb").write_text("not a database", encoding="utf-8")

            report = l4.confirmed_event_graph_report(temp_dir)

        self.assertEqual(report["events"], [])
        self.assertEqual(
            {exclusion["reason"].split(":", 1)[0] for exclusion in report["exclusions"]},
            {"invalid_artifact", "corpus_missing", "corpus_unavailable"},
        )

    def test_reads_fresh_confirmations_from_article_owned_shards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for article, slug in (("First", "First"), ("Second", "Second")):
                article_dir = pathlib.Path(temp_dir) / slug
                findings_dir = article_dir / "findings"
                findings_dir.mkdir(parents=True)
                con = duckdb.connect(str(article_dir / "provenance.duckdb"))
                provenance.ensure_schema(con)
                con.execute("INSERT INTO rsnap VALUES (?,?,?,?,?)", (article, "2026-01-01", 900, 1, 100))
                con.close()
                confirmation = {
                    "article": article,
                    "status": "confirmed",
                    "thresholds": config.confirmation_thresholds(),
                    "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": 900},
                    "confirmed_episodes": [{
                        "before_revid": 100,
                        "before_timestamp": "2025-01-01T00:00:00Z",
                        "after_revid": 101,
                        "after_timestamp": "2025-01-01T00:10:00Z",
                        "durable_spine_drop": 0.6,
                        "pwr_mass": 100_000,
                        "attribution": {
                            "removed_tokens": 10,
                            "replacement_tokens": 0,
                            "removals_by_editor": [{"editor": "Shared Editor", "tokens": 10}],
                            "replacement_by_editor": [],
                        },
                    }],
                }
                artifact = findings_dir / f"{slug}.l1-confirmation.json"
                artifact.write_text(json.dumps(confirmation), encoding="utf-8")

            report = l4.confirmed_event_graph_report(temp_dir)
            l4.run_confirmed_graph(temp_dir)
            graph_artifact_exists = (
                pathlib.Path(temp_dir) / "_shared" / "findings" / "l4_confirmed_graph.json"
            ).is_file()

        self.assertEqual(len(report["events"]), 2)
        self.assertEqual(report["editors"][0]["editor"], "Shared Editor")
        self.assertEqual(report["editors"][0]["article_count"], 2)
        self.assertEqual(report["exclusions"], [])
        self.assertTrue(graph_artifact_exists)


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
        interval_profile = [{
            "start": "2020-01-01",
            "end": "2021-01-01",
            "size": 1200,
            "pwr_loss": 40.0,
            "pwr_removed": 42000,
            "mature": True,
        }]

        with mock.patch.object(provenance, "ensure_sizes"), \
             mock.patch.object(provenance, "ensure_indexes"), \
             mock.patch.object(provenance, "build_snapshots"), \
             mock.patch.object(drift, "ranked_episodes", return_value=ranked), \
             mock.patch.object(drift, "verdict_dict", return_value={"verdict": "PIVOT?"}), \
             mock.patch.object(drift, "refine", return_value=confirmation), \
             mock.patch.object(drift, "attribute", return_value=attribution), \
             mock.patch.object(drift, "_coarse_profile", return_value=interval_profile), \
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
        self.assertEqual(result["interval_profile"], interval_profile)
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
        self.assertEqual(result["evaluated_candidates"], [
            {
                "candidate_start": "2021-01-01",
                "candidate_end": "2022-01-01",
                "candidate_before_revid": 10,
                "candidate_after_revid": 20,
                "source": "interval",
                "pwr_mass": 100000,
                "peak_pct": 40.0,
                "exact_before_revid": 111,
                "exact_before_timestamp": "2021-06-01T00:00:00Z",
                "exact_after_revid": 112,
                "exact_after_timestamp": "2021-06-02T00:00:00Z",
                "durable_spine_drop": 0.1,
                "decision": "rejected",
                "rejection_reason": "durable_spine_drop_below_threshold",
            },
            {
                "candidate_start": "2023-01-01",
                "candidate_end": "2024-01-01",
                "candidate_before_revid": 30,
                "candidate_after_revid": 40,
                "source": "rolling",
                "pwr_mass": 90000,
                "peak_pct": 24.0,
                "exact_before_revid": 211,
                "exact_before_timestamp": "2023-06-01T00:00:00Z",
                "exact_after_revid": 212,
                "exact_after_timestamp": "2023-06-02T00:00:00Z",
                "durable_spine_drop": 0.3,
                "decision": "confirmed",
                "rejection_reason": None,
            },
        ])


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

    def test_build_snapshots_rolls_back_staged_rows_when_receipt_publication_fails(self):
        self.con.execute(
            "INSERT INTO rsnap VALUES (?,?,?,?,?)", ("A", "2024-01-01", 50, 1, 1)
        )
        picks = [("2024-01-01", 50), ("2025-01-01", 100)]
        with mock.patch.object(provenance, "snapshot_picks", return_value=picks), \
             mock.patch.object(provenance, "tokens_at", return_value=[{
                 "token_id": 2, "o_rev_id": 2,
             }]), \
             mock.patch.object(provenance, "refresh_stable_endpoint",
                               side_effect=RuntimeError("receipt failure")), \
             mock.patch.object(provenance.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "receipt failure"):
                provenance.build_snapshots(self.con, "A")

        self.assertEqual(
            self.con.execute(
                "SELECT snap_date, snap_rev, token_id FROM rsnap WHERE article='A'"
            ).fetchall(),
            [("2024-01-01", 50, 1)],
        )

    def test_snapshot_integrity_detects_semantically_partial_membership(self):
        result = provenance.assess_snapshot_integrity(
            token_rows=13482,
            unique_tokens=13482,
            revision_bytes=199984,
            previous_token_rows=54797,
            next_token_rows=56137,
            previous_revision_bytes=196241,
            next_revision_bytes=201248,
        )

        self.assertEqual(result["status"], "suspect")
        self.assertIn("inconsistent", result["reason"])
        self.assertAlmostEqual(result["metrics"]["previous_token_ratio"], 0.2460, places=3)

    def test_snapshot_integrity_accepts_consistent_membership(self):
        result = provenance.assess_snapshot_integrity(
            token_rows=55000,
            unique_tokens=55000,
            revision_bytes=200000,
            previous_token_rows=54797,
            next_token_rows=56137,
            previous_revision_bytes=196241,
            next_revision_bytes=201248,
        )

        self.assertEqual(result["status"], "complete")
        self.assertIsNone(result["reason"])

    def test_stable_endpoint_falls_back_from_immature_latest_revision(self):
        observed_at = provenance.dt.datetime(2026, 8, 1, tzinfo=provenance.dt.timezone.utc)

        receipt = provenance.select_stable_endpoint([
            ("2026-01-01", 100, "2026-01-01T00:00:00Z"),
            ("2026-07-01", 200, "2026-07-01T00:00:00Z"),
            ("2026-08-01", 300, "2026-07-31T23:00:00Z"),
        ], observed_at)

        self.assertEqual(receipt["status"], "stable")
        self.assertEqual(receipt["latest_seen_revid"], 300)
        self.assertEqual(receipt["selected_revid"], 200)
        self.assertEqual(receipt["excluded_revisions"], [{
            "revision_id": 300,
            "reason": "minimum_survival_not_met",
            "evidence_revid": None,
        }])

    def test_stable_endpoint_returns_unstable_without_mature_candidate(self):
        observed_at = provenance.dt.datetime(2026, 8, 1, tzinfo=provenance.dt.timezone.utc)

        receipt = provenance.select_stable_endpoint([
            ("2026-08-01", 300, "2026-07-31T23:00:00Z"),
        ], observed_at)

        self.assertEqual(receipt["status"], "unstable")
        self.assertIsNone(receipt["selected_revid"])

    def test_stable_endpoint_excludes_invalid_timestamp(self):
        observed_at = provenance.dt.datetime(2026, 8, 1, tzinfo=provenance.dt.timezone.utc)

        receipt = provenance.select_stable_endpoint([
            ("2026-08-01", 300, "not-a-timestamp"),
        ], observed_at)

        self.assertEqual(receipt["status"], "unstable")
        self.assertEqual(receipt["excluded_revisions"][0]["reason"],
                         "invalid_revision_timestamp")

    def test_endpoint_receipt_persists_and_controls_current_horizon(self):
        self.con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", [
            ("A", "2026-07-01", 200, 1, 1),
            ("A", "2026-08-01", 300, 1, 1),
        ])
        self.con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", [
            ("A", 200, "2026-07-01T00:00:00Z", "EditorA"),
            ("A", 300, "2026-07-31T23:00:00Z", "EditorB"),
        ])
        self.con.executemany("INSERT INTO rev_size VALUES (?,?,?)", [
            ("A", 200, 1000), ("A", 300, 1000),
        ])
        provenance.refresh_snapshot_integrity(self.con, "A")

        receipt = provenance.refresh_stable_endpoint(
            self.con,
            "A",
            observed_at=provenance.dt.datetime(
                2026, 8, 1, tzinfo=provenance.dt.timezone.utc
            ),
        )

        self.assertEqual(receipt["selected_revid"], 200)
        self.assertEqual(provenance.load_stable_endpoint(self.con, "A")["selected_revid"], 200)
        self.assertEqual(Corpus(self.con).latest_snapshot("A"), ("2026-07-01", 200))
        self.assertEqual(Corpus(self.con).latest_snap_rev("A"), (200,))

    def test_unstable_current_endpoint_does_not_block_historical_as_of(self):
        self.con.execute(
            "INSERT INTO rsnap VALUES (?,?,?,?,?)", ("A", "2026-08-01", 300, 1, 1)
        )
        self.con.execute("INSERT INTO revisions VALUES (?,?,?,?)", (
            "A", 300, "2026-07-31T23:00:00Z", "EditorA",
        ))
        self.con.execute("INSERT INTO rev_size VALUES (?,?,?)", ("A", 300, 1000))
        provenance.refresh_snapshot_integrity(self.con, "A")
        provenance.refresh_stable_endpoint(
            self.con,
            "A",
            observed_at=provenance.dt.datetime(
                2026, 8, 1, tzinfo=provenance.dt.timezone.utc
            ),
        )

        corpus = Corpus(self.con)
        self.assertIsNone(corpus.latest_snapshot("A"))
        self.assertEqual(corpus.snapshot_as_of("A", "2026-08-01"), ("2026-08-01", 300))

    def test_endpoint_audit_is_report_only_by_default(self):
        self.con.execute(
            "INSERT INTO rsnap VALUES (?,?,?,?,?)", ("A", "2026-01-01", 300, 1, 1)
        )
        self.con.execute("INSERT INTO revisions VALUES (?,?,?,?)", (
            "A", 300, "2026-01-01T00:00:00Z", "EditorA",
        ))

        report = provenance.audit_stable_endpoints(
            self.con,
            ["A"],
            observed_at=provenance.dt.datetime(
                2026, 8, 1, tzinfo=provenance.dt.timezone.utc
            ),
        )

        self.assertEqual(report["totals"]["stable"], 1)
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM endpoint_receipts").fetchone()[0], 0
        )

    def test_publication_trust_withholds_unstable_and_quarantined_evidence(self):
        self.con.execute("INSERT INTO endpoint_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            "A", "current_stable", 300, None, None, None, None, None, "unstable", "[]",
            provenance.STABLE_ENDPOINT_POLICY, "2026-08-01T00:00:00+00:00",
        ))
        artifact = {"corpus_horizon": {"snapshot_revid": 300}}

        unstable = trust.resolve_artifact_trust(self.con, "A", artifact, "l1-confirmation")

        self.assertEqual(unstable["status"], "unstable")
        self.con.execute("DELETE FROM endpoint_receipts WHERE article='A'")
        self.con.execute("INSERT INTO endpoint_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            "A", "current_stable", 300, 300, "2026-08-01", "2026-07-01T00:00:00Z",
            2678400, None, "stable", "[]", provenance.STABLE_ENDPOINT_POLICY,
            "2026-08-01T00:00:00+00:00",
        ))
        self.con.execute("INSERT INTO snapshot_integrity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "A", "2026-08-01", 300, "quarantined", 1, 1, 1000, 0.0, None, None,
            None, None, "invalid", provenance.SNAPSHOT_INTEGRITY_POLICY,
            "2026-08-01T00:00:00+00:00",
        ))

        quarantined = trust.resolve_artifact_trust(
            self.con, "A", artifact, "l1-confirmation"
        )

        self.assertEqual(quarantined["status"], "quarantined")
        self.assertIn("300", quarantined["reason"])

    def test_publication_trust_allows_suspect_but_withholds_missing_integrity_receipt(self):
        self.con.execute("INSERT INTO endpoint_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            "A", "current_stable", 300, 300, "2026-08-01", "2026-07-01T00:00:00Z",
            2678400, None, "stable", "[]", provenance.STABLE_ENDPOINT_POLICY,
            "2026-08-01T00:00:00+00:00",
        ))
        artifact = {"corpus_horizon": {"snapshot_revid": 300}}

        missing = trust.resolve_artifact_trust(self.con, "A", artifact, "l1-confirmation")
        self.con.execute("INSERT INTO snapshot_integrity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "A", "2026-08-01", 300, "suspect", 10, 10, 1000, 0.0, 0.2, 0.2,
            1.0, 1.0, "calibration anomaly", provenance.SNAPSHOT_INTEGRITY_POLICY,
            "2026-08-01T00:00:00+00:00",
        ))
        suspect = trust.resolve_artifact_trust(self.con, "A", artifact, "l1-confirmation")

        self.assertEqual(missing["status"], "legacy_incompatible")
        self.assertEqual(suspect["status"], "published")

    def test_integrity_receipts_persist_metrics_and_filter_quarantine(self):
        snapshots = [
            ("A", "2024-01-01", 100, range(1, 11)),
            ("A", "2024-07-01", 200, [1, 1]),
            ("A", "2025-01-01", 300, range(1, 11)),
        ]
        for article, snap_date, snap_rev, token_ids in snapshots:
            self.con.executemany(
                "INSERT INTO rsnap VALUES (?,?,?,?,?)",
                [(article, snap_date, snap_rev, token_id, 1) for token_id in token_ids],
            )
            self.con.execute("INSERT INTO rev_size VALUES (?,?,?)", (article, snap_rev, 1000))

        receipts = provenance.refresh_snapshot_integrity(self.con, "A")

        self.assertEqual([receipt["status"] for receipt in receipts], [
            "complete", "quarantined", "complete",
        ])
        self.assertEqual(receipts[1]["duplicate_rate"], 0.5)
        self.assertEqual(Corpus(self.con).snapshots("A"), [
            ("2024-01-01", 100), ("2025-01-01", 300),
        ])
        self.assertEqual(Corpus(self.con).snapshot_count("A"), 2)

    def test_integrity_audit_marks_quarantined_article_source_partial(self):
        self.con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", [
            ("A", "2024-01-01", 100, 1, 1),
            ("A", "2024-01-01", 100, 1, 1),
        ])
        self.con.execute("INSERT INTO rev_size VALUES (?,?,?)", ("A", 100, 1000))
        provenance.record_source_state(
            self.con,
            "A",
            source_status="current_complete",
            expected_snapshots=1,
            loaded_snapshots=1,
        )

        report = provenance.audit_snapshot_integrity(self.con, ["A"], persist=True)

        self.assertEqual(report["totals"]["quarantined"], 1)
        self.assertEqual(report["articles"][0]["status"], "quarantined")
        source_state = provenance.load_source_state(self.con, "A")
        self.assertEqual(source_state["source_status"], "partial")
        self.assertIn("failed integrity checks", source_state["reason"])

    def test_integrity_audit_is_report_only_by_default(self):
        self.con.execute(
            "INSERT INTO rsnap VALUES (?,?,?,?,?)", ("A", "2024-01-01", 100, 1, 1)
        )
        self.con.execute("INSERT INTO rev_size VALUES (?,?,?)", ("A", 100, 1000))

        report = provenance.audit_snapshot_integrity(self.con, ["A"])

        self.assertEqual(report["totals"]["complete"], 1)
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM snapshot_integrity").fetchone()[0], 0
        )
        self.assertIsNone(provenance.load_source_state(self.con, "A"))

    def test_audit_snapshots_cli_dispatches_integrity_audit(self):
        report = {
            "article_count": 1,
            "totals": {"complete": 1, "suspect": 0, "quarantined": 0},
            "articles": [],
        }
        connection = mock.MagicMock()
        with mock.patch.object(cli.duckdb, "connect", return_value=connection), \
             mock.patch.object(provenance, "audit_snapshot_integrity", return_value=report) as audit:
            cli.main(["audit-snapshots", "A"])

        audit.assert_called_once_with(connection, ["A"], persist=False)
        connection.close.assert_called_once_with()

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

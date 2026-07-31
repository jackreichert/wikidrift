"""Unit tests for pure engine functions (no network, no DB, no LLM)."""
import importlib.util
import datetime as dt
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import duckdb

from wikidrift import l5_factcheck as fc
from wikidrift import l5_crosslingual as xl
from wikidrift import mscore
from wikidrift import l4
from wikidrift import l5_sources as src
from wikidrift import lexical
from wikidrift import drift
from wikidrift import benchmark
from wikidrift import cli
from wikidrift import stance
from wikidrift import pipeline
from wikidrift import config
from wikidrift import provenance
from wikidrift.registry import focal_entities


def _load_cover_missing_topics_module():
    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / "tools" / "cover_missing_topics.py"
    spec = importlib.util.spec_from_file_location("cover_missing_topics", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cover_missing_topics = _load_cover_missing_topics_module()


class Jaccard(unittest.TestCase):
    def test_partial_overlap(self):
        self.assertAlmostEqual(fc._jaccard({"a", "b"}, {"b", "c"}), 1 / 3)

    def test_disjoint_is_zero(self):
        self.assertEqual(fc._jaccard({"a"}, {"b"}), 0.0)

    def test_both_empty_is_one(self):
        # no sources on either side = vacuously identical (not a divergence signal)
        self.assertEqual(fc._jaccard(set(), set()), 1.0)


class FactcheckResilience(unittest.TestCase):
    def test_cap_langs_respects_positive_limit(self):
        self.assertEqual(fc._cap_langs(["en", "he", "ar"], 2), ["en", "he"])

    def test_cap_langs_no_limit_keeps_all(self):
        self.assertEqual(fc._cap_langs(["en", "he"], None), ["en", "he"])
        self.assertEqual(fc._cap_langs(["en", "he"], 0), ["en", "he"])

    @patch("wikidrift.l5_factcheck._call")
    def test_adjudicate_retries_once_after_malformed_output(self, mock_call):
        mock_call.side_effect = [
            ValueError("Unterminated string starting at line 1"),
            {"questions": [{"question": "q", "verdict": "agree", "note": "ok"}]},
        ]
        out, retry_error = fc._adjudicate_with_retry(client=object(), payload="x", questions=["q"])
        self.assertEqual(out[0]["verdict"], "agree")
        self.assertIn("Unterminated string", retry_error)

    @patch("wikidrift.l5_factcheck._call")
    def test_adjudicate_falls_back_to_insufficient_when_retry_fails(self, mock_call):
        mock_call.side_effect = [ValueError("bad json"), ValueError("still bad")]
        out, retry_error = fc._adjudicate_with_retry(client=object(), payload="x", questions=["q1", "q2"])
        self.assertEqual([row["verdict"] for row in out], ["insufficient", "insufficient"])
        self.assertIn("bad json", retry_error)
        self.assertIn("still bad", retry_error)

    @patch("wikidrift.l5_factcheck._select_established_langs")
    def test_resolve_requested_langs_uses_established_defaults_when_not_provided(self, mock_select):
        mock_select.return_value = ["de", "sv", "en"]
        langs, auto_selected = fc._resolve_requested_langs(None, {"en": "A", "de": "A", "sv": "A"})
        self.assertEqual(langs, ["de", "sv", "en"])
        self.assertTrue(auto_selected)

    def test_resolve_requested_langs_filters_user_langs_to_available_links(self):
        langs, auto_selected = fc._resolve_requested_langs(["en", "xx", "fr"], {"en": "A", "fr": "A"})
        self.assertEqual(langs, ["en", "fr"])
        self.assertFalse(auto_selected)


class AdaptiveL5CapPolicy(unittest.TestCase):
    def test_adaptive_cap_uses_default_when_no_prior_diagnostics(self):
        cap, note = cover_missing_topics._adaptive_l5_cap("Abortion", {}, 6)
        self.assertEqual(cap, 6)
        self.assertIn("no prior diagnostics", note)

    def test_adaptive_cap_reduces_when_success_rate_low(self):
        diagnostics = {
            "Abortion": {
                "langs_count": 6,
                "effective_count": 2,
                "error_count": 4,
            }
        }
        cap, note = cover_missing_topics._adaptive_l5_cap("Abortion", diagnostics, 6)
        self.assertEqual(cap, 3)
        self.assertIn("success=0.33", note)

    def test_adaptive_cap_stays_near_default_when_success_high(self):
        diagnostics = {
            "Abortion": {
                "langs_count": 6,
                "effective_count": 6,
                "error_count": 0,
            }
        }
        cap, note = cover_missing_topics._adaptive_l5_cap("Abortion", diagnostics, 6)
        self.assertEqual(cap, 6)
        self.assertIn("success=1.00", note)

    def test_framing_mode_confirms_then_refreshes_each_topic(self):
        commands, notes = cover_missing_topics._topic_commands(
            "Testland", use_llm=True, include_mscore=False, include_framing=False,
            mode="framing", l5_max_langs=None, required=set(), have=set(),
        )
        self.assertEqual([command[-2:] for command in commands], [
            ["analyze", "Testland"], ["framing", "Testland"],
        ])
        self.assertEqual(notes, [])

    def test_full_mode_includes_crosslingual_coverage(self):
        commands, _ = cover_missing_topics._topic_commands(
            "Testland", use_llm=True, include_mscore=False, include_framing=True,
            mode="full", l5_max_langs=None, required=set(), have=set(),
        )
        stages = [command[-2:] for command in commands]
        self.assertEqual(stages[0], ["analyze", "Testland"])
        self.assertIn(["crosslingual", "Testland"], stages)

    def test_pipeline_mode_only_analyzes_then_runs_pipeline(self):
        commands, notes = cover_missing_topics._topic_commands(
            "Testland", use_llm=False, include_mscore=False, include_framing=False,
            mode="pipeline", l5_max_langs=None, required=set(), have=set(),
        )
        self.assertEqual([command[-2:] for command in commands], [
            ["analyze", "Testland"], ["pipeline", "Testland"],
        ])
        self.assertEqual(notes, [])

    def test_attribution_mode_only_backfills_confirmed_pairs(self):
        commands, notes = cover_missing_topics._topic_commands(
            "Testland", use_llm=False, include_mscore=False, include_framing=False,
            mode="attribution", l5_max_langs=None, required=set(), have=set(),
        )
        self.assertEqual(commands, [[
            sys.executable, "-m", "wikidrift.cli", "backfill-attribution", "Testland",
        ]])
        self.assertEqual(notes, [])

    def test_fill_mode_uses_crosslingual_for_stance_and_receipts(self):
        commands, _ = cover_missing_topics._topic_commands(
            "Testland", use_llm=True, include_mscore=False, include_framing=False,
            mode="fill", l5_max_langs=None, required={"stance", "receipts"}, have=set(),
        )
        self.assertEqual([command[-2:] for command in commands], [
            ["analyze", "Testland"], ["crosslingual", "Testland"],
        ])

    def test_cost_report_combines_stage_time_and_framing_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            findings = pathlib.Path(directory)
            (findings / "Testland.framing.json").write_text(json.dumps({
                "llm_usage": {
                    "calls": 2, "input_tokens": 100, "output_tokens": 20,
                    "estimated_usd": 0.0012, "all_calls_priced": True, "records": [],
                }
            }), encoding="utf-8")
            report = cover_missing_topics._write_cost_report(findings, "Testland", [
                {"command": "analyze", "elapsed_seconds": 2.5, "exit_code": 0},
                {"command": "framing", "elapsed_seconds": 3.25, "exit_code": 0},
            ])

            saved = json.loads((findings / "Testland.cost.json").read_text(encoding="utf-8"))
        self.assertEqual(report["elapsed_seconds"], 5.75)
        self.assertTrue(report["succeeded"])
        self.assertEqual(saved["estimated_external_usd"], 0.0012)
        self.assertIn("machine time", saved["estimate_scope"])

    def test_cost_report_marks_failed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            report = cover_missing_topics._write_cost_report(pathlib.Path(directory), "Testland", [
                {"command": "analyze", "elapsed_seconds": 2.5, "exit_code": 0},
                {"command": "framing", "elapsed_seconds": 3.25, "exit_code": 1},
            ])
        self.assertFalse(report["succeeded"])


class ParallelTopicCoverage(unittest.TestCase):
    def test_fresh_confirmed_shard_topics_excludes_stale_and_rejected_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            articles_dir = pathlib.Path(temp_dir)
            for article, status, saved_revid in (
                ("Fresh", "confirmed", 900),
                ("Stale", "confirmed", 899),
                ("Rejected", "not_confirmed", 900),
            ):
                article_dir = articles_dir / article
                findings_dir = article_dir / "findings"
                findings_dir.mkdir(parents=True)
                con = duckdb.connect(str(article_dir / "provenance.duckdb"))
                provenance.ensure_schema(con)
                con.execute("INSERT INTO rsnap VALUES (?,?,?,?,?)", (article, "2026-01-01", 900, 1, 100))
                con.close()
                confirmation = {
                    "article": article,
                    "status": status,
                    "thresholds": config.confirmation_thresholds(),
                    "corpus_horizon": {
                        "snapshot_date": "2026-01-01", "snapshot_revid": saved_revid,
                    },
                }
                (findings_dir / f"{article}.l1-confirmation.json").write_text(
                    json.dumps(confirmation), encoding="utf-8",
                )

            topics = cover_missing_topics._fresh_confirmed_shard_topics(articles_dir)

        self.assertEqual(topics, {"Fresh"})

    def test_analysis_outcome_reads_confirmation_status(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = pathlib.Path(directory)
            findings = data_dir / "findings"
            findings.mkdir()
            (findings / "Testland.l1-confirmation.json").write_text(
                json.dumps({"status": "not_confirmed"}), encoding="utf-8",
            )
            self.assertEqual(
                cover_missing_topics._analysis_outcome(data_dir, "Testland"),
                "not_confirmed",
            )

    def test_analysis_outcome_is_unknown_when_artifact_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                cover_missing_topics._analysis_outcome(pathlib.Path(directory), "Testland"),
                "unknown",
            )

    def test_canonicalize_topics_replaces_redirects_and_deduplicates_targets(self):
        resolved = {
            "Democratic Party of the United States": "Democratic Party (United States)",
            "Democratic Party (United States)": "Democratic Party (United States)",
        }

        def resolve(topic):
            return Mock(requested_title=topic, canonical_title=resolved[topic], page_id=5043544)

        topics, identities = cover_missing_topics._canonicalize_topics(
            list(resolved), resolver=resolve,
        )

        self.assertEqual(topics, ["Democratic Party (United States)"])
        self.assertEqual([identity.requested_title for identity in identities], list(resolved))

        with tempfile.TemporaryDirectory() as directory:
            articles_dir = pathlib.Path(directory)
            cover_missing_topics._write_article_identities(articles_dir, identities)
            identity = json.loads((
                articles_dir / "Democratic_Party_(United_States)" / "article-identity.json"
            ).read_text(encoding="utf-8"))

        self.assertEqual(identity["canonical_title"], "Democratic Party (United States)")
        self.assertEqual(identity["page_id"], 5043544)
        self.assertEqual(identity["requested_titles"], list(resolved))

    def test_project_command_reuses_current_python_environment(self):
        self.assertEqual(
            cover_missing_topics._project_command(["analyze", "Testland"]),
            [sys.executable, "-m", "wikidrift.cli", "analyze", "Testland"],
        )

    def test_stage_name_extracts_project_module_command(self):
        command = cover_missing_topics._project_command(["backfill-attribution", "Testland"])

        self.assertEqual(cover_missing_topics._stage_name(command), "backfill-attribution")

    def test_streaming_command_logs_and_prefixes_each_output_line(self):
        process = Mock(stdout=iter(["first line\n", "last line"]))
        process.wait.return_value = 0
        process_factory = Mock(return_value=process)
        log = io.StringIO()
        output = io.StringIO()

        completed = cover_missing_topics._run_streaming_command(
            cover_missing_topics._project_command(["analyze", "Testland"]),
            env={"PYTHONUNBUFFERED": "1"},
            log=log,
            topic="Testland",
            output_lock=threading.Lock(),
            output_stream=output,
            process_factory=process_factory,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(log.getvalue(), "first line\nlast line")
        self.assertEqual(output.getvalue(), "[Testland] first line\n[Testland] last line\n")

    def test_streaming_command_kills_child_when_output_fails(self):
        process = Mock(stdout=iter(["first line\n"]))
        process_factory = Mock(return_value=process)
        output = Mock()
        output.write.side_effect = OSError("terminal closed")

        with self.assertRaisesRegex(OSError, "terminal closed"):
            cover_missing_topics._run_streaming_command(
                cover_missing_topics._project_command(["analyze", "Testland"]),
                env={"PYTHONUNBUFFERED": "1"},
                log=io.StringIO(),
                topic="Testland",
                output_lock=threading.Lock(),
                output_stream=output,
                process_factory=process_factory,
            )

        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()

    def test_run_topic_isolates_storage_and_resumes_completed_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            articles_dir = pathlib.Path(directory)
            data_dir = articles_dir / "Testland"
            data_dir.mkdir()
            (data_dir / "coverage-state.json").write_text(json.dumps({
                "completed_stages": ["analyze"],
            }), encoding="utf-8")
            runner = Mock(return_value=Mock(returncode=0))

            result = cover_missing_topics._run_topic_commands(
                topic="Testland",
                commands=[
                    cover_missing_topics._project_command(["analyze", "Testland"]),
                    cover_missing_topics._project_command(["pipeline", "Testland"]),
                ],
                articles_dir=articles_dir,
                resume=True,
                runner=runner,
            )

            self.assertTrue(result["succeeded"])
            self.assertEqual(result["skipped_stages"], ["analyze"])
            runner.assert_called_once()
            self.assertEqual(
                runner.call_args.args[0],
                cover_missing_topics._project_command(["pipeline", "Testland"]),
            )
            self.assertEqual(runner.call_args.kwargs["env"]["WIKIDRIFT_DATA_DIR"], str(data_dir))
            state = json.loads((data_dir / "coverage-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["completed_stages"], ["analyze", "pipeline"])

    def test_run_topic_stops_and_preserves_state_on_stage_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            articles_dir = pathlib.Path(directory)
            runner = Mock(side_effect=[Mock(returncode=0), Mock(returncode=1)])

            result = cover_missing_topics._run_topic_commands(
                topic="Testland",
                commands=[
                    cover_missing_topics._project_command(["analyze", "Testland"]),
                    cover_missing_topics._project_command(["pipeline", "Testland"]),
                    cover_missing_topics._project_command(["profile", "Testland"]),
                ],
                articles_dir=articles_dir,
                resume=True,
                runner=runner,
            )

            self.assertFalse(result["succeeded"])
            self.assertEqual([stage["command"] for stage in result["stages"]],
                             ["analyze", "pipeline"])
            state = json.loads((articles_dir / "Testland" / "coverage-state.json").read_text())
            self.assertEqual(state["completed_stages"], ["analyze"])

    def test_run_topic_item_returns_failure_when_worker_raises(self):
        with unittest.mock.patch.object(
            cover_missing_topics, "_run_topic_commands", side_effect=ValueError("bad command")
        ):
            result = cover_missing_topics._run_topic_item(
                ("Testland", [["bad"]]),
                articles_dir=pathlib.Path("articles"),
                resume=False,
            )

        self.assertFalse(result["succeeded"])
        self.assertEqual(result["error"], "bad command")

    def test_run_topics_rejects_duplicate_topics(self):
        with self.assertRaisesRegex(ValueError, "duplicate topics"):
            cover_missing_topics._run_topics_parallel(
                topic_commands=[("Testland", []), ("Testland", [])],
                articles_dir=pathlib.Path("articles"),
                jobs=2,
                resume=False,
            )

    def test_run_topics_uses_requested_worker_limit(self):
        executor = Mock()
        executor.__enter__ = Mock(return_value=executor)
        executor.__exit__ = Mock(return_value=False)
        executor.map.return_value = []
        executor_factory = Mock(return_value=executor)

        results = cover_missing_topics._run_topics_parallel(
            topic_commands=[],
            articles_dir=pathlib.Path("articles"),
            jobs=3,
            resume=False,
            executor_factory=executor_factory,
        )

        self.assertEqual(results, [])
        executor_factory.assert_called_once_with(max_workers=3)


class StanceValue(unittest.TestCase):
    def test_neutral_is_zero(self):
        self.assertEqual(xl._sval({"stance": "neutral"}), 0)

    def test_missing_record_is_none(self):
        self.assertIsNone(xl._sval(None))


class EnglishGap(unittest.TestCase):
    def test_gap_is_english_minus_mean_of_others(self):
        vals = {"en": {"X": 2}, "he": {"X": 0}, "ar": {"X": 0}}
        self.assertEqual(xl._en_gap(vals, ["X"]), 2.0)

    def test_no_others_is_zero(self):
        self.assertEqual(xl._en_gap({"en": {"X": 2}}, ["X"]), 0.0)


class CrosslingualDefaultLangs(unittest.TestCase):
    @patch("wikidrift.l5_crosslingual.prose_asof")
    def test_select_established_langs_prefers_longer_topic_prose(self, mock_prose_asof):
        links = {"en": "A", "fr": "A", "he": "A", "ar": "A"}
        lengths = {"en": 100, "fr": 5000, "he": 2000, "ar": 1500}
        mock_prose_asof.side_effect = lambda lang, title, ts=None: "x" * lengths[lang]
        out = xl._select_established_langs(links, max_langs=3)
        self.assertEqual(out, ["fr", "he", "en"])

    @patch("wikidrift.l5_crosslingual.prose_asof")
    def test_select_established_langs_keeps_english_anchor_when_available(self, mock_prose_asof):
        links = {"en": "A", "de": "A", "fr": "A"}
        lengths = {"en": 1, "de": 4000, "fr": 3000}
        mock_prose_asof.side_effect = lambda lang, title, ts=None: "x" * lengths[lang]
        out = xl._select_established_langs(links, max_langs=2)
        self.assertEqual(out, ["de", "en"])

    @patch("wikidrift.l5_crosslingual.prose_asof")
    def test_pinned_langs_always_included_and_extras_filled_by_prose(self, mock_prose_asof):
        # SLATE pins en/he/ar; de/fr are longer but should still fill extra slots
        links = {"en": "A", "he": "A", "ar": "A", "de": "A", "fr": "A"}
        lengths = {"en": 100, "he": 2000, "ar": 1500, "de": 8000, "fr": 7000}
        mock_prose_asof.side_effect = lambda lang, title, ts=None: "x" * lengths[lang]
        # cap = max(3, 3+2) = 5; pinned=[en,he,ar]; extras=[de,fr]
        out = xl._select_established_langs(links, pinned=["en", "he", "ar"])
        self.assertEqual(out, ["en", "he", "ar", "de", "fr"])


class MScore(unittest.TestCase):
    def test_no_revisions_scores_zero(self):
        r = mscore.mscore([])
        self.assertEqual(r["revs"], 0)
        self.assertEqual(r["M"], 0)

    def test_detects_a_sustained_mutual_revert_pair(self):
        # Alice and Bob edit-war: Alice keeps restoring state S1, Bob keeps restoring S2. Each restore of a
        # non-adjacent earlier sha1 reverts the other, so pairs[(Alice,Bob)] and pairs[(Bob,Alice)] each reach
        # the ≥2 sustained threshold → one mutual pair. M = |warriors| · Σ min(edits_a, edits_b) = 2 · 3 = 6.
        revs = [{"user": "Alice", "sha1": "S1"}, {"user": "Bob", "sha1": "S2"},
                {"user": "Alice", "sha1": "S1"}, {"user": "Bob", "sha1": "S2"},
                {"user": "Alice", "sha1": "S1"}, {"user": "Bob", "sha1": "S2"}]
        r = mscore.mscore(revs)
        self.assertEqual(r["mutual_pairs"], 1)
        self.assertEqual(r["M"], 6)

    def test_a_single_revert_is_below_the_sustained_threshold(self):
        # one back-and-forth only (each direction once) < min_each=2 ⇒ not counted as a mutual-revert war.
        revs = [{"user": "Alice", "sha1": "S1"}, {"user": "Bob", "sha1": "S2"},
                {"user": "Alice", "sha1": "S1"}, {"user": "Bob", "sha1": "S2"}]
        r = mscore.mscore(revs)
        self.assertEqual(r["mutual_pairs"], 0)
        self.assertEqual(r["M"], 0)

    def test_registered_only_excludes_anonymous_ip_editors(self):
        # an IPv4 "user" is anonymous; registered_only (default) drops it before scoring.
        revs = [{"user": "1.2.3.4", "sha1": "S1"}, {"user": "Alice", "sha1": "S2"}]
        self.assertEqual(mscore.mscore(revs)["revs"], 1)
        self.assertEqual(mscore.mscore(revs, registered_only=False)["revs"], 2)


class FocalPassage(unittest.TestCase):
    def test_keeps_only_sentences_mentioning_a_focal_entity(self):
        prose = "Israel did X. The weather is nice. Palestinians said Y."
        out = stance.focal_passage(prose, ["Israel", "Palestinians"])
        self.assertEqual(out, "Israel did X. Palestinians said Y.")
        self.assertNotIn("weather", out)

    def test_entity_match_is_case_insensitive(self):
        self.assertEqual(stance.focal_passage("israel acted.", ["Israel"]), "israel acted.")

    def test_no_matching_sentence_returns_empty(self):
        self.assertEqual(stance.focal_passage("The sky is blue.", ["Israel"]), "")

    def test_truncates_to_max_chars(self):
        prose = "Israel " + "x" * 100 + "."
        self.assertEqual(len(stance.focal_passage(prose, ["Israel"], max_chars=20)), 20)


class FocalRegistry(unittest.TestCase):
    def test_out_of_slate_article_uses_title_as_focal(self):
        self.assertEqual(focal_entities("Chess"), ["Chess"])

    def test_empty_article_returns_no_focal_entities(self):
        self.assertEqual(focal_entities(""), [])


class LexicalMath(unittest.TestCase):
    def test_js_divergence_zero_on_identical_distributions(self):
        a = {"term": 10, "x": 5}
        b = {"term": 20, "x": 10}
        self.assertEqual(lexical._js_divergence(a, b), 0.0)

    def test_log_odds_splits_gained_and_lost(self):
        before = {"old": 10, "stay": 5}
        after = {"new": 10, "stay": 5}
        gained, lost = lexical._log_odds(before, after, min_total=1, top_n=5)
        self.assertTrue(any(r["term"] == "new" for r in gained))
        self.assertTrue(any(r["term"] == "old" for r in lost))


class ReadGap(unittest.TestCase):
    # the pivot-relative read: PEELED AWAY / converged / no net change, with a ±0.25 dead-band.
    def test_peeled_away_above_the_quarter_point_band(self):
        self.assertEqual(xl._read_gap(0.0, 0.3), "PEELED AWAY")

    def test_converged_below_the_band(self):
        self.assertEqual(xl._read_gap(1.0, 0.7), "converged")

    def test_no_net_change_inside_the_band(self):
        self.assertEqual(xl._read_gap(1.0, 1.2), "no net change")   # +0.2 < 0.25
        self.assertEqual(xl._read_gap(1.0, 0.8), "no net change")   # −0.2 > −0.25


class L4Discovery(unittest.TestCase):
    def _confirmation(self, article="Example", editor="Editor A", snapshot_revid=900):
        return {
            "article": article,
            "status": "confirmed",
            "thresholds": config.confirmation_thresholds(),
            "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": snapshot_revid},
            "confirmed_episodes": [{
                "before_revid": 100,
                "before_timestamp": "2025-01-01T00:00:00Z",
                "after_revid": 101,
                "after_timestamp": "2025-01-01T00:10:00Z",
                "candidate_start": "2024-01-01",
                "durable_spine_drop": 0.6,
                "pwr_mass": 100_000,
                "attribution": {
                    "removed_tokens": 10,
                    "replacement_tokens": 4,
                    "removals_by_editor": [{"editor": editor, "tokens": 10}],
                    "replacement_by_editor": [{"editor": editor, "tokens": 4}],
                },
            }],
        }

    def test_seed_uses_fresh_exact_attribution(self):
        editors, metadata = l4.seed_removing_editors(
            self._confirmation(), ("2026-01-01", 900), top_n=4,
        )

        self.assertEqual(editors, [("Editor A", 10)])
        self.assertEqual(metadata["episode"]["before_revid"], 100)
        self.assertEqual(metadata["removed_count"], 10)

    def test_seed_rejects_stale_confirmation(self):
        editors, metadata = l4.seed_removing_editors(
            self._confirmation(), ("2026-01-01", 901), top_n=4,
        )

        self.assertEqual(editors, [])
        self.assertEqual(metadata["reason"], "stale_confirmation")

    def test_seed_rejects_mismatched_attribution_total(self):
        confirmation = self._confirmation()
        confirmation["confirmed_episodes"][0]["attribution"]["removed_tokens"] = 11

        editors, metadata = l4.seed_removing_editors(confirmation, ("2026-01-01", 900))

        self.assertEqual(editors, [])
        self.assertEqual(metadata["reason"], "removal_attribution_mismatch")

    def test_seed_rejects_confirmed_result_without_episodes(self):
        confirmation = self._confirmation()
        confirmation["confirmed_episodes"] = []

        editors, metadata = l4.seed_removing_editors(confirmation, ("2026-01-01", 900))

        self.assertEqual(editors, [])
        self.assertEqual(metadata["reason"], "confirmed_episode_missing")

    def test_seed_selects_highest_mass_exact_episode(self):
        confirmation = self._confirmation(editor="Lower Mass")
        higher_mass = self._confirmation(editor="Higher Mass")["confirmed_episodes"][0]
        higher_mass["pwr_mass"] = 200_000
        higher_mass["before_revid"] = 200
        confirmation["confirmed_episodes"].append(higher_mass)

        editors, metadata = l4.seed_removing_editors(confirmation, ("2026-01-01", 900))

        self.assertEqual(editors, [("Higher Mass", 10)])
        self.assertEqual(metadata["episode"]["before_revid"], 200)

    def test_graph_ranks_literal_editor_by_confirmed_article_breadth(self):
        first = self._confirmation(article="First")
        second = self._confirmation(article="Second")
        second["confirmed_episodes"][0]["before_revid"] = 200
        second["confirmed_episodes"][0]["after_revid"] = 201
        graph = l4.confirmed_event_graph([
            (first, ("2026-01-01", 900)),
            (second, ("2026-01-01", 900)),
        ])

        self.assertEqual(graph["exclusions"], [])
        self.assertEqual(graph["events"][0]["article"], "First")
        self.assertEqual(graph["editors"][0]["editor"], "Editor A")
        self.assertEqual(graph["editors"][0]["article_count"], 2)
        self.assertEqual(graph["editors"][0]["event_count"], 2)
        self.assertEqual(graph["editors"][0]["removed_tokens"], 20)

    def test_graph_excludes_stale_and_bot_attribution(self):
        stale = self._confirmation(article="Stale")
        bot = self._confirmation(article="Bot Event", editor="ExampleBot")
        hidden = self._confirmation(article="Hidden Event", editor="<hidden>")
        graph = l4.confirmed_event_graph([
            (stale, ("2026-01-01", 901)),
            (bot, ("2026-01-01", 900)),
            (hidden, ("2026-01-01", 900)),
        ])

        self.assertEqual(graph["editors"], [])
        self.assertTrue(all(not event["eligible_removing_editors"] for event in graph["events"]))
        self.assertEqual(graph["exclusions"][0]["reason"], "stale_confirmation")

    def test_classify_requires_exact_confirmation(self):
        confirmed = {
            "status": "confirmed",
            "confirmed_episodes": [{"pwr_mass": 100_000}],
            "age_at_pivot": l4.MATURE_PRIOR_YEARS + 1,
        }
        rejected = {"status": "not_confirmed", "coarse_verdict": "PIVOT?"}

        self.assertEqual(l4._classify(confirmed), "confirmed-retrofit-lead")
        self.assertEqual(l4._classify(rejected), "not_confirmed")

    def test_classify_demotes_confirmed_low_mass_event(self):
        result = {
            "status": "confirmed",
            "confirmed_episodes": [{"pwr_mass": l4.MASS_FLOOR - 1}],
            "age_at_pivot": l4.MATURE_PRIOR_YEARS + 1,
        }

        self.assertEqual(l4._classify(result), "confirmed-low-mass")

    def test_retest_runs_full_exact_analysis_and_measures_stable_prior(self):
        exact_result = self._confirmation(article="Candidate")
        with patch.object(l4.drift, "analyze", return_value=exact_result) as analyze, \
             patch.object(l4, "Corpus") as corpus_type:
            corpus_type.return_value.first_revision_ts.return_value = "2010-01-01T00:00:00Z"

            results = l4.retest(object(), ["Candidate"])

        analyze.assert_called_once_with("Candidate", con=unittest.mock.ANY, persist=False)
        self.assertEqual(results[0]["status"], "confirmed")
        self.assertGreater(results[0]["age_at_pivot"], l4.MATURE_PRIOR_YEARS)

    def test_findings_lists_only_independently_confirmed_rewrite_leads(self):
        confirmation = self._confirmation()
        episode = confirmation["confirmed_episodes"][0]
        confirmed = {**confirmation, "age_at_pivot": 10.0}
        rejected = {"article": "Rejected", "status": "not_confirmed"}
        classifications = {
            "Example": "confirmed-retrofit-lead",
            "Rejected": "not_confirmed",
        }

        findings = l4._build_findings(
            "Example", 4, 12, episode, {"removed_count": 10}, [("Editor A", 10)], [],
            [confirmed, rejected], classifications, [confirmed], [],
        )

        self.assertEqual(findings["confirmed_rewrite_leads"], ["Example"])
        self.assertEqual(findings["retrofit_leads"], ["Example"])
        self.assertNotIn("Rejected", findings["confirmed_rewrite_leads"])
        self.assertEqual(findings["semantic_role"], "search_prior")

    def test_cli_dispatches_offline_confirmed_graph(self):
        with patch.object(l4, "run_confirmed_graph") as run_confirmed_graph:
            cli.main(["confirmed-graph", "/tmp/article-shards", "--json"])

        run_confirmed_graph.assert_called_once_with(pathlib.Path("/tmp/article-shards"), as_json=True)

    def test_norm_treats_underscores_as_spaces(self):
        self.assertEqual(l4._norm("Bar_Kokhba_Revolt"), "Bar Kokhba Revolt")
        self.assertEqual(l4._norm("  Palestine  "), "Palestine")
        self.assertEqual(l4._norm(None), "")

    def test_rank_prefers_co_occurrence_then_bytes(self):
        # a candidate touched by MORE seed editors outranks one with more bytes but fewer editors —
            # co-occurrence is the real graph signal (safeguard #3 weights convergent removals).
        agg = {
            "Two editors small": {"editors": {"a", "b"}, "removed": 2_000, "edits": 2},
            "One editor huge":   {"editors": {"a"},      "removed": 500_000, "edits": 1},
            "Two editors big":   {"editors": {"a", "b"}, "removed": 9_000, "edits": 3},
        }
        ranked = [t for t, _ in l4.rank_candidates(agg, limit=10)]
        self.assertEqual(ranked, ["Two editors big", "Two editors small", "One editor huge"])

    def test_rank_respects_limit(self):
        agg = {f"t{i}": {"editors": {"a"}, "removed": i, "edits": 1} for i in range(20)}
        self.assertEqual(len(l4.rank_candidates(agg, limit=5)), 5)

    def test_jsonable_serializes_sets(self):
        out = l4._jsonable({"editors": {"b", "a"}, "n": 1, "nested": [{"s": {"x"}}]})
        self.assertEqual(out, {"editors": ["a", "b"], "n": 1, "nested": [{"s": ["x"]}]})

    # _classify — exact confirmation is mandatory before a graph-surfaced rewrite lead can exist.
    def test_classify_confirmed_retrofit_lead(self):
        r = {"status": "confirmed", "confirmed_episodes": [{"pwr_mass": l4.MASS_FLOOR + 1}],
             "age_at_pivot": l4.MATURE_PRIOR_YEARS + 1}
        self.assertEqual(l4._classify(r), "confirmed-retrofit-lead")

    def test_classify_confirmed_born_in_contested_when_prior_too_young(self):
        r = {"status": "confirmed", "confirmed_episodes": [{"pwr_mass": l4.MASS_FLOOR + 1}],
             "age_at_pivot": l4.MATURE_PRIOR_YEARS - 1}
        self.assertEqual(l4._classify(r), "confirmed-born-in-contested")

    def test_classify_missing_age_remains_unknown(self):
        result = {"status": "confirmed", "confirmed_episodes": [{"pwr_mass": l4.MASS_FLOOR + 1}]}
        self.assertEqual(l4._classify(result), "confirmed-age-unknown")

    def test_classify_non_confirmed_results(self):
        self.assertEqual(l4._classify({"status": "not_confirmed"}), "not_confirmed")
        self.assertEqual(l4._classify({"status": "unavailable"}), "unavailable")


class DriftEpisodes(unittest.TestCase):
    def test_age_years_and_future_clamp(self):
        self.assertAlmostEqual(drift._age_years("2024-01-01", "2026-01-01"), 2.0, delta=0.02)
        self.assertEqual(drift._age_years("2030-01-01", "2026-01-01"), 0.0)   # end after horizon → clamped

    def test_recency_tag(self):
        self.assertEqual(drift._recency_tag(1.0), "recent")
        self.assertEqual(drift._recency_tag(10.0), "standing 10yr")

    def test_creep_or_healthy_label(self):
        self.assertEqual(drift._creep_or_healthy_label(drift.CREEP_MEAN + 1), "CREEP")
        self.assertEqual(drift._creep_or_healthy_label(drift.CREEP_MEAN - 1), "HEALTHY/stable")

    def test_build_episodes_groups_contiguous_and_ranks(self):
        # rows: (d0, r0, d1, r1, ratio%, size, pwr_removed)
        series = [
            ("2020-01-01", 1, "2020-07-01", 2, 5.0, 1000, 50),    # below ELEVATED — ignored
            ("2020-07-01", 2, "2021-01-01", 3, 20.0, 1000, 200),  # starts an episode
            ("2021-01-01", 3, "2021-07-01", 4, 30.0, 1000, 300),  # time-contiguous → extends it
            ("2021-07-01", 4, "2022-01-01", 5, 5.0, 1000, 40),    # below → closes the episode
            ("2023-01-01", 6, "2023-07-01", 7, 40.0, 1000, 400),  # a second, separate episode
        ]
        eps = drift.build_episodes(series)
        self.assertEqual(len(eps), 2)
        self.assertEqual((eps[0]["abs"], eps[0]["peak"]), (500, 30.0))   # 200+300 accumulated; peak = max
        self.assertEqual((eps[0]["start"], eps[0]["end"]), (("2020-07-01", 2), ("2021-07-01", 4)))
        self.assertEqual(eps[1]["abs"], 400)
        self.assertTrue(all(episode["source"] == "interval" for episode in eps))

    def test_annotate_ranks_by_pwr_mass_and_sets_age(self):
        eps = [{"start": ("2020-01-01", 1), "end": ("2020-07-01", 2), "abs": 100, "peak": 20},
               {"start": ("2024-01-01", 3), "end": ("2024-07-01", 4), "abs": 500, "peak": 30}]
        out = drift.annotate_episodes(eps, "2026-01-01")
        self.assertEqual([e["abs"] for e in out], [500, 100])    # ranked by PWR-mass, age-agnostic
        self.assertGreater(out[1]["age"], out[0]["age"])         # the older episode carries the larger age

    def test_rolling_candidates_detects_repeated_medium_loss_over_one_year(self):
        snaps = [("2022-01-01", 1), ("2022-07-01", 2), ("2023-01-01", 3)]
        members = [set(range(100)), set(range(90)), set(range(70))]
        present = {token: [index for index, member in enumerate(members) if token in member]
                   for token in range(100)}

        candidates = drift.rolling_candidates(
            snaps, members, present, min_mature=50, threshold=20.0, mass_floor=20,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual((candidates[0]["start"][0], candidates[0]["end"][0]),
                         ("2022-01-01", "2023-01-01"))
        self.assertEqual(candidates[0]["source"], "rolling")
        self.assertGreaterEqual(candidates[0]["peak"], 20.0)

    def test_rolling_candidates_skip_sparse_and_immature_starts(self):
        snaps = [("2022-01-01", 1), ("2022-03-01", 2), ("2023-07-01", 3)]
        members = [set(range(10)), set(range(100)), set(range(50))]
        present = {token: [index for index, member in enumerate(members) if token in member]
                   for token in range(100)}

        candidates = drift.rolling_candidates(
            snaps, members, present, min_mature=50, threshold=20.0, mass_floor=20,
        )

        self.assertEqual(candidates, [])

    def test_non_overlapping_candidates_keeps_highest_mass_window(self):
        candidates = [
            {"start": ("2022-01-01", 1), "end": ("2023-01-01", 2), "abs": 100, "peak": 30},
            {"start": ("2022-07-01", 3), "end": ("2023-07-01", 4), "abs": 200, "peak": 35},
            {"start": ("2024-01-01", 5), "end": ("2025-01-01", 6), "abs": 150, "peak": 25},
        ]

        selected = drift.non_overlapping_candidates(candidates)

        self.assertEqual([candidate["abs"] for candidate in selected], [200, 150])

    def test_non_overlapping_candidates_respects_primary_blocked_windows(self):
        blocked = [{"start": ("2022-01-01", 1), "end": ("2023-01-01", 2), "abs": 300}]
        candidates = [
            {"start": ("2022-07-01", 3), "end": ("2023-07-01", 4), "abs": 200},
            {"start": ("2024-01-01", 5), "end": ("2025-01-01", 6), "abs": 150},
        ]

        selected = drift.non_overlapping_candidates(candidates, blocked=blocked)

        self.assertEqual([candidate["abs"] for candidate in selected], [150])


class DriftProfile(unittest.TestCase):
    def test_concentration_empty(self):
        self.assertEqual(drift._concentration({}), (0.0, 0))

    def test_concentration_all_within_top10(self):
        self.assertEqual(drift._concentration({"a": 5, "b": 3, "c": 2}), (100.0, 3))

    def test_concentration_beyond_top10(self):
        counts = {"e0": 100, **{f"e{i}": 1 for i in range(1, 15)}}   # 15 editors, 114 tokens
        share, n = drift._concentration(counts)
        self.assertEqual(n, 15)
        self.assertEqual(share, 95.6)   # top-10 = 100 + 9×1 = 109 of 114


class L5Sources(unittest.TestCase):
    def test_composition_reads_cite_types_and_refs(self):
        raw = ("Text.<ref>{{cite journal|url=https://doi.org/x}}</ref> "
               "more<ref>{{cite news|url=http://www.nytimes.com/a}}</ref> "
               "<ref>{{cite web|url=https://example.org/b}}</ref>")
        c = src.composition(raw)
        self.assertEqual(c["refs"], 3)
        self.assertEqual(c["cite_types"], {"journal": 1, "news": 1, "web": 1})
        # www. stripped; domains counted
        self.assertIn("nytimes.com", c["domains"])
        self.assertEqual(c["n_domains"], 3)

    def test_tld_bucket(self):
        self.assertEqual(src._tld_bucket("harvard.edu"), "edu")
        self.assertEqual(src._tld_bucket("state.gov"), "gov/int")
        self.assertEqual(src._tld_bucket("un.org"), "org")   # plain .org unless it's the un.org gov/int case
        self.assertEqual(src._tld_bucket("example.com"), "com")

    def test_wayback_unwraps_to_real_source(self):
        raw = ("<ref>{{cite news|url=https://web.archive.org/web/20200101000000/https://www.nytimes.com/x}}</ref>"
               "<ref>{{cite web|url=https://web.archive.org/web/2019/https://bbc.co.uk/y}}</ref>")
        d = src._source_domains(raw)
        self.assertEqual(d.get("nytimes.com"), 1)   # archived NYT counts as nytimes, not web.archive.org
        self.assertEqual(d.get("bbc.co.uk"), 1)
        self.assertNotIn("web.archive.org", d)

    def test_deltas_added_and_dropped_from_to(self):
        early = {"jstor.org": 5, "oldsource.example": 4}
        late = {"jstor.org": 6, "newsource.example": 51}
        added, dropped = src._deltas(early, late)
        # added: newsource 0→51 ranks first by absolute increase
        self.assertEqual((added[0][0], added[0][2], added[0][3]), ("newsource.example", 0, 51))
        # dropped: oldsource 4→0 surfaces as a source moved away from
        self.assertEqual((dropped[0][0], dropped[0][2], dropped[0][3]), ("oldsource.example", 4, 0))


class _FakeStanceClient:
    """A stub LLM client: returns the stance encoded as 'STANCE:<x>' in the passage, for every focal
    entity named in the prompt. Mocks only the external boundary (the model), never wikidrift's own code."""
    def complete_json(self, schema, prompt, max_tokens=0):
        import re
        st = (re.search(r"STANCE:(\w+)", prompt) or [None, "neutral"])[1]
        ents_m = re.search(r"Focal entities: ([^\n]+)", prompt)
        ents = [e.strip() for e in ents_m.group(1).split(",")] if ents_m else []
        return {"entities": [{"entity": e, "stance": st, "npov_departure": False, "evidence": ""} for e in ents]}


class StaticDivergence(unittest.TestCase):
    """The cross-edition spread math (l5_crosslingual.static_divergence) with the LLM client stubbed."""
    def test_lead_divergence_is_max_minus_min_stance_across_editions(self):
        prose = {"en": "STANCE:critical", "he": "STANCE:neutral", "ar": "STANCE:neutral"}
        stat = xl.static_divergence(_FakeStanceClient(), "X", ["en", "he", "ar"], prose,
                                    ["Israel"], {"Israel": {}})
        # en=critical(-1), he/ar=neutral(0) ⇒ spread = 0 − (−1) = 1
        self.assertEqual(stat["variants"]["lead"]["divergence"], 1.0)
        self.assertEqual(stat["variants"]["lead"]["spreads"], {"Israel": 1})

    def test_full_agreement_is_zero_divergence(self):
        prose = {"en": "STANCE:neutral", "he": "STANCE:neutral"}
        stat = xl.static_divergence(_FakeStanceClient(), "X", ["en", "he"], prose, ["Israel"], {"Israel": {}})
        self.assertEqual(stat["variants"]["lead"]["divergence"], 0.0)


class BenchmarkScoring(unittest.TestCase):
    """score_case's per-category grading, with the engine (verdict_dict/prerank) injected — pure logic."""
    FLOOR = benchmark.MASS_FLOOR

    def _score(self, cat, L1, leads=()):
        case = {"article": "X", "cat": cat}
        with patch.object(benchmark, "verdict_dict", lambda con, art: L1), \
             patch.object(benchmark, "prerank", lambda con, art: {"leads": list(leads)}):
            return benchmark.score_case(None, case)["status"]

    def test_A_removal_pass_partial_fail(self):
        self.assertEqual(self._score("A_removal", {"verdict": "PIVOT?", "top_mass": self.FLOOR}), "PASS")
        self.assertEqual(self._score("A_removal", {"verdict": "PIVOT?", "top_mass": self.FLOOR - 1}), "PARTIAL")
        self.assertEqual(self._score("A_removal", {"verdict": "HEALTHY", "top_mass": 0}), "FAIL")

    def test_B_addition_needs_an_L2_route(self):
        h = {"verdict": "HEALTHY", "top_mass": 0}
        self.assertEqual(self._score("B_addition", h, ["addition→L2"]), "PASS")
        self.assertEqual(self._score("B_addition", h, ["churn→L2"]), "PASS")
        self.assertEqual(self._score("B_addition", h, []), "FAIL")

    def test_D_benign_demotes_low_mass_flags_large(self):
        self.assertEqual(self._score("D_benign", {"verdict": "HEALTHY", "top_mass": 0}), "PASS")
        self.assertEqual(self._score("D_benign", {"verdict": "PIVOT?", "top_mass": self.FLOOR - 1}), "PASS")   # demoted
        self.assertEqual(self._score("D_benign", {"verdict": "PIVOT?", "top_mass": self.FLOOR + 1}), "PARTIAL")

    def test_E_clean_false_positive_on_large_pivot(self):
        self.assertEqual(self._score("E_clean", {"verdict": "HEALTHY", "top_mass": 0}), "PASS")
        self.assertEqual(self._score("E_clean", {"verdict": "PIVOT?", "top_mass": self.FLOOR + 1}), "FAIL")

    def test_E_clean_low_mass_pivot_is_demoted_not_a_false_positive(self):
        # a small pivot on a clean control is demoted (PASS), not counted as a false positive.
        self.assertEqual(self._score("E_clean", {"verdict": "PIVOT?", "top_mass": self.FLOOR - 1}), "PASS")

    def test_C_l5gap_is_an_expected_miss(self):
        self.assertEqual(self._score("C_l5gap", {"verdict": "HEALTHY", "top_mass": 0}), "L5-GAP")

    def _detail(self, L1, leads=()):
        with patch.object(benchmark, "verdict_dict", lambda con, art: L1), \
             patch.object(benchmark, "prerank", lambda con, art: {"leads": list(leads)}):
            return benchmark.score_case(None, {"article": "X", "cat": "C_l5gap"})["detail"]

    def test_C_l5gap_unexpected_l1_flag_is_flagged_for_investigation(self):
        # an L1 PIVOT on a born-biased control is not expected — the detail must call for investigation.
        d = self._detail({"verdict": "PIVOT?", "top_mass": self.FLOOR + 1})
        self.assertIn("investigate", d)

    def test_C_l5gap_prerank_l2_route_is_noted_as_a_lead(self):
        # L1 stays HEALTHY but the pre-ranker routes to L2 → noted as a pre-ranker lead (still needs L5).
        d = self._detail({"verdict": "HEALTHY", "top_mass": 0}, ["addition→L2"])
        self.assertIn("pre-ranker", d)

    def test_skip_verdict_is_pending(self):
        self.assertEqual(self._score("A_removal", {"verdict": "SKIP"}), "PENDING")

    def test_pending_case_short_circuits(self):
        r = benchmark.score_case(None, {"article": "X", "cat": "C_l5gap", "pending": True})
        self.assertEqual(r["status"], "PENDING")


class ConcentrationCalibration(unittest.TestCase):
    def _confirmation(self):
        return {
            "article": "Example",
            "status": "confirmed",
            "thresholds": config.confirmation_thresholds(),
            "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": 900},
            "confirmed_episodes": [{
                "before_revid": 100,
                "after_revid": 200,
                "durable_spine_drop": 0.62,
                "pwr_mass": 120000,
                "attribution": {
                    "duration_seconds": 1080,
                    "removed_tokens": 10,
                    "replacement_tokens": 8,
                    "removals_by_editor": [
                        {"editor": "Editor A", "tokens": 7},
                        {"editor": "Editor B", "tokens": 3},
                    ],
                    "replacement_by_editor": [{"editor": "Editor A", "tokens": 8}],
                },
            }],
        }

    def test_extracts_recomputable_raw_event_without_label(self):
        dataset = benchmark.concentration_dataset([
            (self._confirmation(), ("2026-01-01", 900)),
        ])

        self.assertFalse(dataset["labels_enabled"])
        self.assertEqual(dataset["exclusions"], [])
        event = dataset["events"][0]
        self.assertEqual(event["top_removal_share"], 0.7)
        self.assertEqual(event["top_two_removal_share"], 1.0)
        self.assertTrue(event["same_top_editor"])
        self.assertNotIn("label", event)

    def test_stale_confirmation_is_excluded(self):
        dataset = benchmark.concentration_dataset([
            (self._confirmation(), ("2026-01-01", 901)),
        ])

        self.assertEqual(dataset["events"], [])
        self.assertEqual(dataset["exclusions"][0]["reason"], "stale_confirmation")

    def test_missing_attribution_is_excluded_with_reason(self):
        confirmation = self._confirmation()
        episode = confirmation["confirmed_episodes"][0]
        episode["attribution"] = None
        episode["attribution_unavailable"] = "token provenance unavailable"

        dataset = benchmark.concentration_dataset([
            (confirmation, ("2026-01-01", 900)),
        ])

        self.assertEqual(dataset["events"], [])
        self.assertEqual(dataset["exclusions"][0]["reason"], "token provenance unavailable")

    def test_mismatched_raw_counts_fail_closed(self):
        confirmation = self._confirmation()
        confirmation["confirmed_episodes"][0]["attribution"]["removed_tokens"] = 11

        with self.assertRaisesRegex(ValueError, "removal attribution total"):
            benchmark.concentration_dataset([(confirmation, ("2026-01-01", 900))])

    def test_mismatched_replacement_counts_fail_closed(self):
        confirmation = self._confirmation()
        confirmation["confirmed_episodes"][0]["attribution"]["replacement_tokens"] = 9

        with self.assertRaisesRegex(ValueError, "replacement attribution total"):
            benchmark.concentration_dataset([(confirmation, ("2026-01-01", 900))])

    def test_unconfirmed_artifact_is_excluded(self):
        confirmation = self._confirmation()
        confirmation["status"] = "not_confirmed"

        dataset = benchmark.concentration_dataset([
            (confirmation, ("2026-01-01", 900)),
        ])

        self.assertEqual(dataset["events"], [])
        self.assertEqual(dataset["exclusions"][0]["reason"], "not_confirmed")

    def test_summary_reports_feature_ranges_without_thresholds(self):
        events = benchmark.concentration_dataset([
            (self._confirmation(), ("2026-01-01", 900)),
        ])["events"]

        summary = benchmark.concentration_summary(events)

        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["top_removal_share"]["median"], 0.7)
        self.assertEqual(summary["same_top_editor_count"], 1)
        self.assertNotIn("threshold", summary)

    def test_readiness_rejects_non_discriminating_editor_shares(self):
        summary = {
            "top_removal_share": {"count": 2, "min": 1.0, "median": 1.0, "max": 1.0},
            "top_replacement_share": {"count": 2, "min": 1.0, "median": 1.0, "max": 1.0},
            "top_two_removal_share": {"count": 2, "min": 1.0, "median": 1.0, "max": 1.0},
        }

        readiness = benchmark.concentration_readiness(summary)

        self.assertFalse(readiness["calibration_ready"])
        self.assertEqual(len(readiness["calibration_blockers"]), 3)

    def test_cli_dispatches_offline_concentration_report(self):
        with patch.object(benchmark, "run_concentration") as run_concentration:
            cli.main(["calibrate-concentration", "/tmp/article-shards", "--json"])

        run_concentration.assert_called_once_with(pathlib.Path("/tmp/article-shards"), as_json=True)


class PipelineCorroboration(unittest.TestCase):
    """_corroboration: count how many independent layers fire."""

    def test_zero_signals_when_all_healthy(self):
        result = {"l1": "HEALTHY (mean 2.0%)", "l2_adjudicated": False,
                  "lexical": {"js_divergence": 0.01}, "mscore": None}
        c = pipeline._corroboration(result)
        self.assertEqual(c["count"], 0)
        self.assertEqual(c["signals"], [])

    def test_l1_pivot_fires_when_not_healthy(self):
        result = {"l1": "PIVOT? 2020-01-01→2022-01-01 ...", "l2_adjudicated": False,
                  "lexical": None, "mscore": None}
        c = pipeline._corroboration(result)
        self.assertIn("l1_pivot", c["signals"])

    def test_fresh_rejection_suppresses_stale_coarse_pivot(self):
        result = {
            "l1": "PIVOT? 2020-01-01→2022-01-01 ...",
            "l1_state": {"confirmation_status": "not_confirmed"},
            "lexical": None,
            "mscore": None,
        }
        self.assertNotIn("l1_pivot", pipeline._corroboration(result)["signals"])

    def test_l1_creep_also_fires(self):
        result = {"l1": "CREEP? mean 9.2%", "l2_adjudicated": False,
                  "lexical": None, "mscore": None}
        self.assertIn("l1_pivot", pipeline._corroboration(result)["signals"])

    def test_l2_adjudicated_with_shift_adds_shift_signal(self):
        result = {"l1": "HEALTHY (mean 2.0%)", "l2_adjudicated": True,
                  "l2": {"shifts": {"Testland": {"start": 0, "end": -1, "shifted": True, "n": 2}}},
                  "lexical": None, "mscore": None}
        self.assertIn("l2_shift", pipeline._corroboration(result)["signals"])

    def test_l2_adjudicated_but_flat_does_not_add_shift_signal(self):
        result = {"l1": "HEALTHY (mean 2.0%)", "l2_adjudicated": True,
                  "l2": {"shifts": {"Testland": {"start": 0, "end": 0, "shifted": False, "n": 2}}},
                  "lexical": None, "mscore": None}
        self.assertNotIn("l2_shift", pipeline._corroboration(result)["signals"])

    def test_lexical_drift_fires_above_threshold(self):
        result = {"l1": "HEALTHY", "l2_adjudicated": False,
                  "lexical": {"mode": "pivot_relative", "adequate": True,
                              "js_divergence": 0.08}, "mscore": None}
        self.assertIn("lexical_drift", pipeline._corroboration(result)["signals"])

    def test_whole_history_lexical_change_does_not_corroborate(self):
        result = {"l1": "HEALTHY", "l2_adjudicated": False,
                  "lexical": {"mode": "whole_history", "adequate": True,
                              "js_divergence": 0.08}, "mscore": None}
        self.assertNotIn("lexical_drift", pipeline._corroboration(result)["signals"])

    def test_lexical_drift_does_not_fire_below_threshold(self):
        result = {"l1": "HEALTHY", "l2_adjudicated": False,
                  "lexical": {"js_divergence": 0.03}, "mscore": None}
        self.assertNotIn("lexical_drift", pipeline._corroboration(result)["signals"])

    def test_count_matches_signals_length(self):
        result = {"l1": "PIVOT?", "l2_adjudicated": True,
                  "l2": {"shifts": {"Testland": {"start": -1, "end": 1, "shifted": True, "n": 2}}},
                  "lexical": {"mode": "pivot_relative", "adequate": True,
                              "js_divergence": 0.09}, "mscore": None}
        c = pipeline._corroboration(result)
        self.assertEqual(c["count"], len(c["signals"]))

    def test_skip_verdict_does_not_fire_l1(self):
        result = {"l1": "SKIP (too few snapshots)", "l2_adjudicated": False,
                  "lexical": None, "mscore": None}
        self.assertNotIn("l1_pivot", pipeline._corroboration(result)["signals"])


class L4AggregateFootprint(unittest.TestCase):
    """footprint(): aggregate-per-editor-per-article threshold (not per-edit)."""

    @patch("wikidrift.l4._usercontribs")
    def test_fifty_small_removals_sum_above_threshold(self, mock_contribs):
        # 50 edits × 1400B each = 70 000B total > REMOVAL_BYTES(1500) → should qualify
        mock_contribs.return_value = [
            {"title": "New Article", "sizediff": -1400, "timestamp": "2023-01-01T00:00:00Z"}
            for _ in range(50)
        ]
        editors = [("Alice", 100)]
        agg = l4.footprint(editors, tested=set(), seed_article="SeedArticle")
        self.assertIn("New Article", agg)
        self.assertGreaterEqual(agg["New Article"]["removed"], 70_000)

    @patch("wikidrift.l4._usercontribs")
    def test_single_edit_below_threshold_does_not_qualify(self, mock_contribs):
        # One edit removing 1400B < REMOVAL_BYTES(1500) → should NOT qualify
        mock_contribs.return_value = [
            {"title": "New Article", "sizediff": -1400, "timestamp": "2023-01-01T00:00:00Z"}
        ]
        editors = [("Alice", 100)]
        agg = l4.footprint(editors, tested=set(), seed_article="SeedArticle")
        self.assertNotIn("New Article", agg)

    @patch("wikidrift.l4._usercontribs")
    def test_tested_articles_excluded_from_footprint(self, mock_contribs):
        mock_contribs.return_value = [
            {"title": "Known Article", "sizediff": -50_000, "timestamp": "2023-01-01T00:00:00Z"}
        ]
        editors = [("Alice", 100)]
        agg = l4.footprint(editors, tested={"Known Article"}, seed_article="SeedArticle")
        self.assertNotIn("Known Article", agg)


from wikidrift import l5_framing_lite as fl


class FramingLiteEditionSelect(unittest.TestCase):
    """_select_editions: SLATE + top-N by length, deduplicated and capped."""

    def _links(self, langs):
        return {l: f"Title_{l}" for l in langs}

    def test_divergence_schemas_bound_text_without_unsupported_max_items(self):
        for schema in (fl._DIVERGENCE_SCHEMA, fl._TEMPORAL_DIVERGENCE_SCHEMA):
            self.assertNotIn("maxItems", schema["properties"]["divergences"])
            self.assertEqual(schema["properties"]["summary"]["maxLength"], fl.MAX_SUMMARY_CHARS)
        self.assertNotIn("maxItems", fl._DIVERGENCE_ITEM["properties"]["editions_differ"])
        temporal = fl._TEMPORAL_DIVERGENCE_SCHEMA["properties"]["divergences"]["items"]
        self.assertEqual(
            temporal["properties"]["evidence_en_before"]["anyOf"][0]["maxLength"],
            fl.MAX_EVIDENCE_CHARS,
        )
        self.assertEqual(
            temporal["properties"]["evidence_en_before"]["anyOf"][1],
            {"type": "null"},
        )

    def test_bound_comparison_caps_divergences_and_editions_without_mutating_input(self):
        source = {
            "divergences": [
                {"topic": str(index), "editions_differ": ["en", "ar", "he", "ja", "ur", "de"]}
                for index in range(fl.MAX_DIVERGENCES + 2)
            ],
            "summary": "bounded",
        }
        result = fl._bound_comparison(source)
        self.assertEqual(len(result["divergences"]), fl.MAX_DIVERGENCES)
        self.assertEqual(len(result["divergences"][0]["editions_differ"]), fl.MAX_EDITIONS)
        self.assertEqual(len(source["divergences"]), fl.MAX_DIVERGENCES + 2)
        self.assertEqual(len(source["divergences"][0]["editions_differ"]), fl.MAX_EDITIONS + 1)

    def test_ground_evidence_keeps_exact_quotes_and_drops_unsupported_text(self):
        source = {
            "divergences": [{
                "evidence_en": "An exact quotation",
                "evidence_other": "A model paraphrase",
            }],
            "summary": "comparison",
        }

        result = fl._ground_evidence(source, {
            "evidence_en": ["Lead containing an exact quotation here."],
            "evidence_other": ["The source says something else."],
        })

        self.assertEqual(result["divergences"][0]["evidence_en"], "An exact quotation")
        self.assertIsNone(result["divergences"][0]["evidence_other"])
        self.assertEqual(source["divergences"][0]["evidence_other"], "A model paraphrase")

    def test_ground_evidence_normalizes_unicode_punctuation_and_whitespace(self):
        result = fl._ground_evidence(
            {"divergences": [{"evidence_en": "A “quoted” phrase — here"}]},
            {"evidence_en": ['A  "quoted" phrase - here']},
        )

        self.assertEqual(result["divergences"][0]["evidence_en"], "A “quoted” phrase — here")

    def test_temporal_prompt_requests_bounded_concise_output(self):
        class CapturingClient:
            prompt = None

            def complete_json(self, _schema, prompt, max_tokens=0):
                self.prompt = prompt
                return {"divergences": [], "summary": "aligned"}

        client = CapturingClient()
        snapshots = {
            "before": {"en": {"revid": 1, "timestamp": "2020-01-01", "lead": "before"},
                       "fr": {"revid": 2, "timestamp": "2020-01-01", "lead": "avant"}},
            "after": {"en": {"revid": 3, "timestamp": "2021-01-01", "lead": "after"},
                      "fr": {"revid": 4, "timestamp": "2021-01-01", "lead": "apres"}},
        }
        fl._compare_temporal_leads(
            "Testland", snapshots,
            {"start": "2020-01-01", "end": "2021-01-01", "status": "candidate"},
            client,
        )
        self.assertIn(f"at most {fl.MAX_DIVERGENCES}", client.prompt)
        self.assertIn("shortest supporting quotations", client.prompt)
        self.assertIn("Do not label any edition biased", client.prompt)

    def test_slate_langs_always_included(self):
        links = self._links(["en", "ar", "he", "fr", "de"])
        lengths = {"ar": 5000, "he": 4000, "fr": 3000, "de": 2000}
        eds = fl._select_editions("israeli-palestinian", links, lengths)
        self.assertIn("ar", eds)
        self.assertIn("he", eds)
        self.assertIn("en", eds)

    def test_top2_by_length_added_to_slate(self):
        links = self._links(["en", "ar", "he", "fr", "de", "es"])
        lengths = {"ar": 5000, "he": 4000, "fr": 9000, "de": 8000, "es": 1000}
        eds = fl._select_editions("israeli-palestinian", links, lengths)
        # ar + he are SLATE; fr + de are top-2 by length; es is below MIN_EDITION_BYTES
        self.assertIn("fr", eds)
        self.assertIn("de", eds)
        self.assertNotIn("es", eds)

    def test_cap_at_max_editions(self):
        links = self._links(["en", "ar", "he", "fr", "de", "es", "ru", "zh"])
        lengths = {l: 5000 for l in ["ar", "he", "fr", "de", "es", "ru", "zh"]}
        eds = fl._select_editions("israeli-palestinian", links, lengths)
        self.assertLessEqual(len(eds), fl.MAX_EDITIONS)

    def test_general_category_uses_length_only(self):
        links = self._links(["en", "fr", "de", "es"])
        lengths = {"fr": 9000, "de": 8000, "es": 3000}
        eds = fl._select_editions("general", links, lengths)
        self.assertIn("en", eds)
        self.assertIn("fr", eds)
        self.assertIn("de", eds)
        # No SLATE for general — just top-2 by length
        self.assertLessEqual(len(eds), fl.MAX_EDITIONS)

    def test_stub_editions_excluded(self):
        links = self._links(["en", "fr", "de"])
        lengths = {"fr": 9000, "de": 500}  # de is below MIN_EDITION_BYTES
        eds = fl._select_editions("general", links, lengths)
        self.assertIn("fr", eds)
        self.assertNotIn("de", eds)

    def test_en_always_first(self):
        links = self._links(["en", "ar", "he"])
        lengths = {"ar": 5000, "he": 4000}
        eds = fl._select_editions("israeli-palestinian", links, lengths)
        self.assertEqual(eds[0], "en")

    def test_no_duplicates(self):
        # ar is both SLATE and top by length — should appear once
        links = self._links(["en", "ar", "he", "fr"])
        lengths = {"ar": 99000, "he": 4000, "fr": 3000}
        eds = fl._select_editions("israeli-palestinian", links, lengths)
        self.assertEqual(len(eds), len(set(eds)))

    def test_historical_lead_uses_only_lead_section(self):
        raw = "Lead text with [[Link|label]].\n\n== History ==\nBody text."
        self.assertEqual(fl._lead_from_wikitext(raw), "Lead text with label.")

    @patch.object(fl._S, "get")
    def test_historical_fetch_selects_first_revision_after_window(self, mock_get):
        mock_get.return_value.json.return_value = {"query": {"pages": [{"revisions": [{
            "revid": 123,
            "timestamp": "2020-02-01T00:00:00Z",
            "slots": {"main": {"content": "Historical lead.\n\n== Body ==\nLater text."}},
        }]}]}}

        record = fl._fetch_lead_revision("en", "Article", "2020-01-01T00:00:00Z", after=True)

        self.assertEqual(record["revid"], 123)
        self.assertEqual(record["lead"], "Historical lead.")
        self.assertEqual(mock_get.call_args.kwargs["params"]["rvdir"], "newer")

    @patch.object(fl._S, "get")
    def test_confirmed_english_fetch_uses_exact_revision(self, mock_get):
        mock_get.return_value.json.return_value = {"query": {"pages": [{"revisions": [{
            "revid": 456,
            "timestamp": "2020-02-01T00:00:00Z",
            "slots": {"main": {"content": "Confirmed lead."}},
        }]}]}}

        record = fl._fetch_lead_by_revid("en", "Article", 456)

        self.assertEqual(record["revid"], 456)
        self.assertEqual(mock_get.call_args.kwargs["params"]["revids"], 456)

    def test_early_exit_persists_current_usage_instead_of_leaving_stale_finding(self):
        client = type("Client", (), {"model": "m", "usage_records": []})()
        record = {
            "provider": "openai", "model": "m", "input_tokens": 8, "output_tokens": 2,
            "estimated_usd": None, "pricing_key": None, "pricing_usd_per_million": None,
        }

        def categorize(_article, used_client):
            used_client.usage_records.append(record)
            return {"category": "general", "confidence": 1.0}

        with patch.object(fl, "_categorize", side_effect=categorize), \
             patch.object(fl, "sitelinks", side_effect=LookupError("missing")), \
             patch.object(fl.config, "write_findings") as write_findings:
            result = fl.framing_lite("Testland", client=client)
        saved = write_findings.call_args.args[1]

        self.assertEqual(result["error"], "missing")
        self.assertEqual(saved["llm_usage"]["input_tokens"], 8)
        self.assertEqual(saved["llm_usage"]["calls"], 1)

    def test_comparison_failure_persists_error_and_usage_before_reraising(self):
        record = {
            "provider": "anthropic", "model": "m", "input_tokens": 20, "output_tokens": 10,
            "estimated_usd": None, "pricing_key": None, "pricing_usd_per_million": None,
        }

        class FailingClient:
            model = "m"
            usage_records = []

            def complete_json(self, _schema, _prompt, max_tokens=0):
                self.usage_records.append(record)
                raise RuntimeError("invalid JSON twice")

        client = FailingClient()
        with patch.object(fl, "_categorize", return_value={"category": "general", "confidence": 1.0}), \
             patch.object(fl, "sitelinks", return_value=("Q1", {"en": "Testland", "fr": "Testland"})), \
             patch.object(fl, "_edition_lengths", return_value={"fr": 5000}), \
             patch.object(fl, "_fetch_lead", side_effect=["English lead", "French lead"]), \
             patch.object(fl.config, "write_findings") as write_findings:
            with self.assertRaisesRegex(RuntimeError, "invalid JSON twice"):
                fl.framing_lite("Testland", client=client)

        saved = write_findings.call_args.args[1]
        self.assertEqual(saved["error"], "invalid JSON twice")
        self.assertEqual(saved["llm_usage"]["calls"], 1)
        self.assertEqual(saved["summary"], "LLM comparison failed; no framing result was produced.")


class PipelinePivotWindow(unittest.TestCase):
    def test_top_l1_episode_becomes_explicit_candidate_context(self):
        verdict = {"episodes": [
            {"start": "2019-01-01", "end": "2020-01-01", "pwr_mass": 42000},
            {"start": "2021-01-01", "end": "2022-01-01", "pwr_mass": 10000},
        ]}
        self.assertEqual(pipeline._pivot_window(verdict), {
            "start": "2019-01-01", "end": "2020-01-01", "pwr_mass": 42000,
            "status": "candidate",
        })

    def test_no_l1_episode_keeps_framing_static(self):
        self.assertIsNone(pipeline._pivot_window({"episodes": []}))

    def test_fresh_confirmation_supplies_exact_pivot_pair(self):
        confirmation = {
            "status": "confirmed",
            "corpus_horizon": {"snapshot_date": "2024-01-01", "snapshot_revid": 900},
            "thresholds": {
                "confirm_drop": config.CONFIRM_DROP, "durable_quantile": config.DURABLE_Q,
                "min_cohort": config.MIN_COHORT, "magnitude_floor": config.MAG_FLOOR,
                "rolling_window_months": config.ROLLING_WINDOW_MONTHS,
                "rolling_tolerance_days": config.ROLLING_TOLERANCE_DAYS,
                "rolling_drop": config.ROLLING_DROP,
            },
            "confirmed_episodes": [{
                "candidate_start": "2020-01-01", "candidate_end": "2021-01-01",
                "before_revid": 111, "before_timestamp": "2020-06-01T00:00:00Z",
                "after_revid": 112, "after_timestamp": "2020-06-02T00:00:00Z",
                "durable_spine_drop": 0.4, "pwr_mass": 42000,
            }],
        }
        window = pipeline._confirmed_window(confirmation, ("2024-01-01", 900))
        self.assertEqual(window["status"], "confirmed")
        self.assertEqual((window["before_revid"], window["after_revid"]), (111, 112))

    def test_stale_confirmation_is_rejected(self):
        confirmation = {
            "status": "confirmed",
            "corpus_horizon": {"snapshot_date": "2023-01-01", "snapshot_revid": 800},
            "thresholds": {
                "confirm_drop": config.CONFIRM_DROP, "durable_quantile": config.DURABLE_Q,
                "min_cohort": config.MIN_COHORT, "magnitude_floor": config.MAG_FLOOR,
                "rolling_window_months": config.ROLLING_WINDOW_MONTHS,
                "rolling_tolerance_days": config.ROLLING_TOLERANCE_DAYS,
                "rolling_drop": config.ROLLING_DROP,
            },
            "confirmed_episodes": [{"candidate_start": "2020-01-01"}],
        }
        self.assertIsNone(pipeline._confirmed_window(confirmation, ("2024-01-01", 900)))

    def test_fresh_not_confirmed_result_is_authoritative(self):
        confirmation = {
            "status": "not_confirmed",
            "corpus_horizon": {"snapshot_date": "2024-01-01", "snapshot_revid": 900},
            "thresholds": config.confirmation_thresholds(),
            "confirmed_episodes": [],
        }
        self.assertTrue(pipeline.confirmation_is_fresh(confirmation, ("2024-01-01", 900)))
        self.assertIsNone(pipeline._confirmed_window(confirmation, ("2024-01-01", 900)))

        state = pipeline.resolve_l1_state(
            {"verdict": "PIVOT?", "episodes": [{"start": "2020-01-01"}]},
            confirmation,
            ("2024-01-01", 900),
        )
        self.assertEqual(state["analysis_status"], "available")
        self.assertEqual(state["candidate_status"], "pivot_candidate")
        self.assertEqual(state["confirmation_status"], "not_confirmed")
        self.assertEqual(state["resolved_status"], "not_confirmed")

    def test_partial_source_coverage_is_unavailable(self):
        state = pipeline.resolve_l1_state(
            {"verdict": "PIVOT?", "episodes": [{"start": "2020-01-01"}]},
            None,
            ("2024-01-01", 900),
            source_state={
                "source_status": "partial",
                "expected_snapshots": 25,
                "loaded_snapshots": 24,
                "reason": "loaded 24 of 25 expected snapshots",
            },
        )
        self.assertEqual(state["analysis_status"], "unavailable")
        self.assertEqual(state["resolved_status"], "unavailable")
        self.assertEqual(state["reason"], "loaded 24 of 25 expected snapshots")

    def test_source_revision_ahead_of_cached_horizon_is_unavailable(self):
        state = pipeline.resolve_l1_state(
            {"verdict": "HEALTHY"},
            None,
            ("2024-01-01", 900),
            source_state={
                "source_status": "current_complete",
                "source_checked_at": "2026-07-30T00:00:00+00:00",
                "source_latest_revid": 901,
            },
            now=dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(state["analysis_status"], "unavailable")
        self.assertEqual(state["resolved_status"], "unavailable")
        self.assertIn("ahead of cached snapshot revision", state["reason"])

    def test_expired_source_check_is_unavailable(self):
        state = pipeline.resolve_l1_state(
            {"verdict": "HEALTHY"},
            None,
            ("2024-01-01", 900),
            source_state={
                "source_status": "current_complete",
                "source_checked_at": "2026-07-01T00:00:00+00:00",
                "source_latest_revid": 900,
            },
            now=dt.datetime(2026, 7, 30, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(state["analysis_status"], "unavailable")
        self.assertEqual(state["resolved_status"], "unavailable")
        self.assertIn("source check expired", state["reason"])

    def test_confirmation_with_old_thresholds_is_rejected(self):
        confirmation = {
            "status": "confirmed",
            "corpus_horizon": {"snapshot_date": "2024-01-01", "snapshot_revid": 900},
            "thresholds": {
                "confirm_drop": 0.1, "durable_quantile": config.DURABLE_Q,
                "min_cohort": config.MIN_COHORT, "magnitude_floor": config.MAG_FLOOR,
                "rolling_window_months": config.ROLLING_WINDOW_MONTHS,
                "rolling_tolerance_days": config.ROLLING_TOLERANCE_DAYS,
                "rolling_drop": config.ROLLING_DROP,
            },
            "confirmed_episodes": [{"candidate_start": "2020-01-01"}],
        }
        self.assertIsNone(pipeline._confirmed_window(confirmation, ("2024-01-01", 900)))


class LexicalModeContract(unittest.TestCase):
    def test_exact_confirmation_supplies_revision_pair(self):
        window = {
            "status": "confirmed",
            "before_revid": 111,
            "before_timestamp": "2025-01-01T00:00:00Z",
            "after_revid": 112,
            "after_timestamp": "2025-01-01T00:18:00Z",
        }
        selected = lexical._window_revs(Mock(), "A", mode="pivot_relative", window=window)

        self.assertEqual((selected["before_rev"], selected["after_rev"]), (111, 112))
        self.assertEqual(selected["interval_source"], "exact_confirmation")

    def test_small_or_imbalanced_baseline_is_insufficient(self):
        adequate, reason, ratio = lexical._baseline_adequacy(
            before_tokens=51,
            after_tokens=1066,
            min_tokens=100,
            max_size_ratio=4.0,
        )

        self.assertFalse(adequate)
        self.assertIn("minimum token floor", reason)
        self.assertGreater(ratio, 20)

    def test_pivot_relative_analysis_fetches_exact_revisions(self):
        window = {
            "status": "confirmed",
            "before_revid": 111,
            "before_timestamp": "2025-01-01T00:00:00Z",
            "after_revid": 112,
            "after_timestamp": "2025-01-01T00:18:00Z",
        }
        with patch.object(lexical.duckdb, "connect", return_value=Mock()), \
             patch.object(
                 lexical,
                 "prose_at",
                 side_effect=["alpha " * 120, "beta " * 120],
             ) as prose_at:
            result = lexical.lexical_drift(
                "A", mode="pivot_relative", window=window, persist=False
            )

        self.assertEqual([call.args[0] for call in prose_at.call_args_list], [111, 112])
        self.assertEqual(result["mode"], "pivot_relative")
        self.assertEqual(result["interval_source"], "exact_confirmation")
        self.assertTrue(result["adequate"])

    def test_not_applicable_skips_corpus_and_prose_reads(self):
        with patch.object(lexical.duckdb, "connect") as connect, \
             patch.object(lexical, "prose_at") as prose_at:
            result = lexical.lexical_drift("A", mode="not_applicable", persist=False)

        self.assertEqual(result["mode"], "not_applicable")
        connect.assert_not_called()
        prose_at.assert_not_called()

    def test_imbalanced_analysis_is_persisted_as_insufficient(self):
        window = {
            "status": "confirmed",
            "before_revid": 111,
            "before_timestamp": "2025-01-01T00:00:00Z",
            "after_revid": 112,
            "after_timestamp": "2025-01-01T00:18:00Z",
        }
        with patch.object(lexical.duckdb, "connect", return_value=Mock()), \
             patch.object(
                 lexical,
                 "prose_at",
                 side_effect=["alpha " * 120, "beta " * 600],
             ):
            result = lexical.lexical_drift(
                "A", mode="pivot_relative", window=window, persist=False
            )

        self.assertEqual(result["mode"], "insufficient_baseline")
        self.assertEqual(result["requested_mode"], "pivot_relative")
        self.assertFalse(result["adequate"])
        self.assertEqual(result["size_ratio"], 5.0)


if __name__ == "__main__":
    unittest.main()

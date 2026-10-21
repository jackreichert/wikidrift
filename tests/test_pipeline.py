"""The L1→L2→L5 orchestration must follow the pre-rank router (the 'adjudicate routed leads' gap).

Offline only (no LLM, no network): asserts the routing DECISION, not the LLM adjudication itself.
"""
import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

from wikidrift import config, drift, pipeline


class PipelineRunMocked(unittest.TestCase):
    """Characterize pipeline.run offline with mocks (no corpus / network)."""

    def _fresh_confirmation(self, **extra):
        base = {
            "schema_version": drift.CONFIRMATION_SCHEMA_VERSION,
            "status": "confirmed",
            "corpus_horizon": {"snapshot_date": "2024-01-01", "snapshot_revid": 900},
            "thresholds": config.confirmation_thresholds(),
            "confirmed_episodes": [{
                "candidate_start": "2020-01-01", "candidate_end": "2021-01-01",
                "before_revid": 111, "before_timestamp": "2020-06-01T00:00:00Z",
                "after_revid": 112, "after_timestamp": "2020-06-02T00:00:00Z",
                "durable_spine_drop": 0.4, "pwr_mass": 42000,
            }],
        }
        base.update(extra)
        return base

    def _common_patches(self, *, snaps=5, confirmation=None, verdict=None, leads=None,
                        source_status="unchecked"):
        confirmation = confirmation if confirmation is not None else {
            "schema_version": drift.CONFIRMATION_SCHEMA_VERSION,
            "status": "not_confirmed",
            "corpus_horizon": {"snapshot_date": "2024-01-01", "snapshot_revid": 900},
            "thresholds": config.confirmation_thresholds(),
            "confirmed_episodes": [],
        }
        verdict = verdict or {"verdict": "PIVOT?", "episodes": [{
            "start": "2020-01-01", "end": "2021-01-01", "pwr_mass": 1000,
        }]}
        leads = leads if leads is not None else ["removal→PWR"]
        mock_corpus = MagicMock()
        mock_corpus.return_value.latest_snapshot.return_value = ("2024-01-01", 900)
        return [
            patch("wikidrift.pipeline.provenance.resolve_article_title",
                  return_value=MagicMock(canonical_title="Testland")),
            patch("wikidrift.pipeline.duckdb.connect", return_value=MagicMock()),
            patch("wikidrift.pipeline._snap_count", return_value=snaps),
            patch("wikidrift.pipeline.drift.verdict_dict", return_value=verdict),
            patch("wikidrift.pipeline.drift.candidate_verdict", return_value=("Testland", "PIVOT?")),
            patch("wikidrift.pipeline.Corpus", mock_corpus),
            patch("wikidrift.pipeline.drift.load_confirmation", return_value=confirmation),
            patch("wikidrift.pipeline.provenance.load_source_state",
                  return_value={"source_status": source_status}),
            patch("wikidrift.pipeline.prerank.prerank", return_value={"leads": leads}),
            patch("wikidrift.pipeline.lexical.lexical_drift", return_value={
                "mode": "not_applicable", "js_divergence": 0.01, "adequate": True,
            }),
        ]

    def test_few_snaps_triggers_analyze(self):
        patches = self._common_patches(snaps=0)
        patches[2] = patch("wikidrift.pipeline._snap_count", side_effect=[0, 5, 5])
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
            patches[6], patches[7], patches[8], patches[9],
            patch("wikidrift.pipeline.drift.analyze") as mock_analyze,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = pipeline.run("Testland")
        mock_analyze.assert_called_once_with("Testland")
        self.assertEqual(result["article"], "Testland")
        self.assertIn("l1_state", result)

    def test_confirmed_windows_use_episode_path(self):
        confirmation = self._fresh_confirmation()
        patches = self._common_patches(confirmation=confirmation, leads=[])
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
            patches[6], patches[7], patches[8],
            patch("wikidrift.pipeline.analyze_confirmed_episodes", return_value={
                "lexical": {"mode": "pivot_relative", "js_divergence": 0.2, "episode_count": 1},
                "sources": {"episode_count": 1},
            }) as mock_episodes,
            patch("wikidrift.pipeline.lexical.lexical_drift") as mock_lexical,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = pipeline.run("Testland")
        mock_episodes.assert_called_once()
        mock_lexical.assert_not_called()
        self.assertEqual(result["l1_state"]["resolved_status"], "confirmed")
        self.assertEqual(result["lexical"]["episode_count"], 1)

    def test_no_confirm_with_framing_uses_static_path(self):
        patches = self._common_patches(leads=[])
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
            patches[6], patches[7], patches[8], patches[9],
            patch("wikidrift.llm.make_client", return_value="client"),
            patch("wikidrift.l5_framing_lite.framing_lite", return_value={
                "divergences": [], "pivot_window": None,
            }) as mock_framing,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = pipeline.run("Testland", framing=True)
        mock_framing.assert_called_once()
        self.assertEqual(result["l5"], {"divergences": [], "pivot_window": None})

    def test_result_keys_stable(self):
        patches = self._common_patches(leads=[])
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
            patches[6], patches[7], patches[8], patches[9],
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = pipeline.run("Testland")
        self.assertEqual(set(result), {
            "article", "l1", "l1_state", "leads", "l2_adjudicated", "l2", "trajectory",
            "mscore", "lexical", "sources", "l5", "facts", "process_context",
        })


@unittest.skipUnless(config.DB.exists(), "provenance.duckdb corpus not present")
class Orchestration(unittest.TestCase):
    def _run(self, article):
        with contextlib.redirect_stdout(io.StringIO()):    # pipeline prints a report; keep test output clean
            return pipeline.run(article, llm=False, corroborate=False)

    def test_nakba_routes_addition_to_L2(self):
        # canonical born-framed / addition case: L1 reads HEALTHY, the router catches reframe-by-addition
        r = self._run("Nakba")
        self.assertIn("HEALTHY", r["l1"])
        self.assertIn("addition→L2", r["leads"])
        self.assertFalse(r["l2_adjudicated"])              # offline ⇒ pending --llm, not adjudicated

    def test_result_shape(self):
        r = self._run("Photosynthesis")
        self.assertEqual(set(r), {
            "article", "l1", "l1_state", "leads", "l2_adjudicated", "l2", "trajectory",
            "mscore", "lexical", "sources", "l5", "facts", "process_context",
        })


if __name__ == "__main__":
    unittest.main()

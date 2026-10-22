"""Characterization tests for CLI confirmed-window routing and simple handlers."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from wikidrift import cli


class ConfirmedWindowDispatch(unittest.TestCase):
    """Pin the four confirmed-or-fallback CLI verbs before Extract Function."""

    def test_factcheck_uses_confirmed_episodes_when_windows_present(self):
        windows = [{"status": "confirmed", "before_revid": 1, "after_revid": 2}]
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=windows) as mock_windows,
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.llm.make_client", return_value="client") as mock_client,
            patch("wikidrift.cli.l5_factcheck.factcheck") as mock_fallback,
        ):
            cli.main(["factcheck", "Testland"])
        mock_windows.assert_called_once_with("Testland")
        mock_client.assert_called_once()
        mock_episodes.assert_called_once()
        kwargs = mock_episodes.call_args.kwargs
        self.assertEqual(mock_episodes.call_args.args[0], "Testland")
        self.assertEqual(mock_episodes.call_args.args[1], windows)
        self.assertTrue(kwargs["facts"])
        self.assertFalse(kwargs["lexical_layer"])
        self.assertFalse(kwargs["sources"])
        mock_fallback.assert_not_called()

    def test_factcheck_skips_windows_when_asof_set(self):
        with (
            patch("wikidrift.cli.pipeline.framing_windows") as mock_windows,
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.cli.l5_factcheck.factcheck") as mock_fallback,
        ):
            cli.main(["factcheck", "Testland", "--asof", "2018-06-01"])
        mock_windows.assert_not_called()
        mock_episodes.assert_not_called()
        mock_fallback.assert_called_once()
        self.assertEqual(mock_fallback.call_args.kwargs["ts"], "2018-06-01T00:00:00Z")

    def test_factcheck_falls_back_when_no_confirmed_windows(self):
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=[{"status": "candidate"}]),
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.cli.l5_factcheck.factcheck") as mock_fallback,
        ):
            cli.main(["factcheck", "Testland"])
        mock_episodes.assert_not_called()
        mock_fallback.assert_called_once_with(
            "Testland", langs=None, ts=None, provider=None, model=None, base_url=None, max_langs=None,
        )

    def test_framing_uses_confirmed_episodes(self):
        windows = [{"status": "confirmed", "before_revid": 1, "after_revid": 2}]
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=windows),
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.llm.make_client", return_value="client"),
            patch("wikidrift.l5_framing_lite.framing_lite") as mock_fallback,
        ):
            cli.main(["framing", "Testland"])
        mock_episodes.assert_called_once()
        self.assertTrue(mock_episodes.call_args.kwargs["framing"])
        mock_fallback.assert_not_called()

    def test_framing_static_skips_windows_and_falls_back(self):
        with (
            patch("wikidrift.cli.pipeline.framing_windows") as mock_windows,
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.l5_framing_lite.framing_lite") as mock_fallback,
        ):
            cli.main(["framing", "Testland", "--static"])
        mock_windows.assert_not_called()
        mock_episodes.assert_not_called()
        mock_fallback.assert_called_once()
        self.assertIsNone(mock_fallback.call_args.kwargs.get("pivot_window")
                          or mock_fallback.call_args.kwargs.get("pivot_window", None))
        # pivot_window should be None when static / empty windows
        self.assertIsNone(mock_fallback.call_args.kwargs["pivot_window"])

    def test_framing_fallback_uses_first_candidate_window(self):
        windows = [{"status": "candidate", "start": "2020-01-01"}]
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=windows),
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.l5_framing_lite.framing_lite") as mock_fallback,
        ):
            cli.main(["framing", "Testland"])
        mock_episodes.assert_not_called()
        self.assertEqual(mock_fallback.call_args.kwargs["pivot_window"], windows[0])

    def test_sources_uses_confirmed_episodes(self):
        windows = [{"status": "confirmed", "before_revid": 1, "after_revid": 2}]
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=windows),
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.cli.l5_sources.sources_over_time") as mock_fallback,
        ):
            cli.main(["sources", "Testland", "--max-snaps", "7"])
        mock_episodes.assert_called_once()
        self.assertFalse(mock_episodes.call_args.kwargs["lexical_layer"])
        self.assertEqual(mock_episodes.call_args.kwargs["source_options"], {"max_snaps": 7})
        mock_fallback.assert_not_called()

    def test_sources_falls_back_without_confirmed(self):
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=[]),
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.cli.l5_sources.sources_over_time") as mock_fallback,
        ):
            cli.main(["sources", "Testland"])
        mock_episodes.assert_not_called()
        mock_fallback.assert_called_once_with("Testland", max_snaps=12)

    def test_lexical_uses_confirmed_episodes(self):
        windows = [{"status": "confirmed", "before_revid": 1, "after_revid": 2}]
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=windows),
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.cli.lexical.lexical_drift") as mock_fallback,
        ):
            cli.main(["lexical", "Testland", "--top-n", "5", "--min-total", "2"])
        mock_episodes.assert_called_once()
        self.assertFalse(mock_episodes.call_args.kwargs["sources"])
        self.assertEqual(
            mock_episodes.call_args.kwargs["lexical_options"],
            {"top_n": 5, "min_total": 2},
        )
        mock_fallback.assert_not_called()

    def test_lexical_falls_back_without_confirmed(self):
        with (
            patch("wikidrift.cli.pipeline.framing_windows", return_value=[]),
            patch("wikidrift.cli.pipeline.analyze_confirmed_episodes") as mock_episodes,
            patch("wikidrift.cli.lexical.lexical_drift") as mock_fallback,
        ):
            cli.main(["lexical", "Testland"])
        mock_episodes.assert_not_called()
        mock_fallback.assert_called_once_with("Testland", top_n=20, min_total=3)


class SimpleHandlerDispatch(unittest.TestCase):
    """Smoke-characterize simple verb handlers used during handler= migration."""

    def test_analyze_handler(self):
        with patch("wikidrift.cli.drift.analyze") as mock_analyze:
            cli.main(["analyze", "Testland"])
        mock_analyze.assert_called_once_with("Testland")

    def test_prerank_handler(self):
        with patch("wikidrift.cli.prerank.run") as mock_run:
            cli.main(["prerank", "A", "B"])
        mock_run.assert_called_once_with(["A", "B"])

    def test_profile_handler(self):
        with patch("wikidrift.cli.drift.profile_report") as mock_profile:
            cli.main(["profile", "https://en.wikipedia.org/wiki/Testland"])
        mock_profile.assert_called_once_with("Testland")

    def test_bootstrap_handler(self):
        with patch("wikidrift.cli.bootstrap.run") as mock_run:
            cli.main(["bootstrap"])
        mock_run.assert_called_once_with(None)

    def test_ingest_handler(self):
        with patch("wikidrift.cli.ingest.ingest_articles") as mock_ingest:
            cli.main(["ingest", "A", "--force"])
        mock_ingest.assert_called_once_with(["A"], force=True)

    def test_ingest_normalizes_wiki_url_before_invoke(self):
        url = "https://en.wikipedia.org/wiki/2026_Iran_war"
        with patch("wikidrift.cli.ingest.ingest_articles") as mock_ingest:
            cli.main(["ingest", url])
        mock_ingest.assert_called_once_with(["2026 Iran war"], force=False)


if __name__ == "__main__":
    unittest.main()

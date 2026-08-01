import json
import pathlib
import tempfile
import unittest
from unittest import mock

from viewer import export_l3
from viewer.export_l3 import published_articles


class PublishedArticleDiscovery(unittest.TestCase):
    def test_discovers_sorted_unique_articles_from_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "z.profile.json").write_text(json.dumps({"article": "Zed"}), encoding="utf-8")
            (root / "a.profile.json").write_text(json.dumps({"article": "Alpha"}), encoding="utf-8")
            (root / "duplicate.profile.json").write_text(json.dumps({"article": "Zed"}), encoding="utf-8")
            (root / "ignored.lexical.json").write_text(json.dumps({"article": "Ignored"}), encoding="utf-8")
            (root / "broken.profile.json").write_text("not json", encoding="utf-8")

            self.assertEqual(published_articles(root), ["Alpha", "Zed"])


class CandidatePivotExport(unittest.TestCase):
    def test_confirmed_export_uses_coarse_candidate_pair(self):
        confirmation = {
            "article": "Example",
            "status": "confirmed",
            "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": 900},
            "thresholds": export_l3.config.confirmation_thresholds(),
            "evaluated_candidates": [{
                "candidate_start": "2024-01-01",
                "candidate_end": "2025-01-01",
                "candidate_before_revid": 90,
                "candidate_after_revid": 110,
                "decision": "confirmed",
                "durable_spine_drop": 0.75,
                "peak_pct": 42.0,
                "pwr_mass": 500,
            }],
            "confirmed_episodes": [{
                "before_revid": 101,
                "before_timestamp": "2025-01-01T00:00:00Z",
                "after_revid": 102,
                "after_timestamp": "2025-01-01T00:20:00Z",
                "duration_seconds": 1200,
                "durable_spine_drop": 0.75,
                "pwr_mass": 500,
                "attribution": {
                    "removed_tokens": 80,
                    "replacement_tokens": 40,
                    "removals_by_editor": [{"editor": "Editor A", "tokens": 80}],
                    "replacement_by_editor": [{"editor": "Editor A", "tokens": 40}],
                    "top_removal_share": 1.0,
                    "top_replacement_share": 1.0,
                    "same_top_editor": True,
                    "top_two_removal_share": 1.0,
                },
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(export_l3, "DATA", pathlib.Path(temp_dir)), \
                mock.patch.object(export_l3.drift, "load_confirmation", return_value=confirmation), \
                mock.patch.object(export_l3, "_confirmation_trust", return_value={
                    "status": "published", "reason": None,
                }), \
                mock.patch.object(export_l3, "_current_horizon", return_value=("2026-01-01", 900)), \
                mock.patch.object(export_l3.provenance, "tokens_at", return_value=[{"str": "word"}]), \
                mock.patch.object(export_l3, "prose_at", side_effect=lambda revision: f"text {revision}"), \
                mock.patch.object(export_l3, "_revision_authors", return_value={}), \
                mock.patch.object(export_l3.drift, "verdict_dict") as coarse_verdict:
            status = export_l3.export_pivots("Example")
            exported = json.loads(
                (pathlib.Path(temp_dir) / "Example.pivots.json").read_text(encoding="utf-8")
            )

        self.assertEqual(status["state"], "finding")
        self.assertEqual(exported["corpus_horizon"]["snapshot_revid"], 900)
        self.assertEqual(exported["pivots"][0]["before_rev"], 90)
        self.assertEqual(exported["pivots"][0]["after_rev"], 110)
        self.assertEqual(exported["pivots"][0]["status"], "confirmed")
        self.assertEqual(exported["pivots"][0]["metric"], "persistence_weighted_loss")
        self.assertEqual(exported["pivots"][0]["before_text"], "text 90")
        self.assertEqual(exported["pivots"][0]["after_text"], "text 110")
        coarse_verdict.assert_not_called()

    def test_rejected_candidate_still_exports_a_redline(self):
        confirmation = {
            "article": "Example",
            "status": "not_confirmed",
            "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": 900},
            "thresholds": export_l3.config.confirmation_thresholds(),
            "evaluated_candidates": [{
                "candidate_start": "2024-01-01",
                "candidate_end": "2025-01-01",
                "candidate_before_revid": 90,
                "candidate_after_revid": 110,
                "decision": "rejected",
                "rejection_reason": "durable_spine_drop_below_threshold",
                "durable_spine_drop": 0.1,
                "peak_pct": 42.0,
                "pwr_mass": 500,
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(export_l3, "DATA", pathlib.Path(temp_dir)), \
                mock.patch.object(export_l3.drift, "load_confirmation", return_value=confirmation), \
                mock.patch.object(export_l3, "_confirmation_trust", return_value={
                    "status": "published", "reason": None,
                }), \
                mock.patch.object(export_l3, "_current_horizon", return_value=("2026-01-01", 900)), \
                mock.patch.object(export_l3.provenance, "tokens_at", return_value=[{"str": "word"}]), \
                mock.patch.object(export_l3, "prose_at", side_effect=lambda revision: f"text {revision}"), \
                mock.patch.object(export_l3, "_revision_authors", return_value={}):
            status = export_l3.export_pivots("Example")
            exported = json.loads(
                (pathlib.Path(temp_dir) / "Example.pivots.json").read_text(encoding="utf-8")
            )

        self.assertEqual(status["state"], "finding")
        self.assertEqual(exported["pivots"][0]["status"], "rejected")
        self.assertEqual(
            exported["pivots"][0]["rejection_reason"],
            "durable_spine_drop_below_threshold",
        )

    def test_withheld_confirmation_removes_existing_export(self):
        confirmation = {"article": "Example", "status": "confirmed"}
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(export_l3, "DATA", pathlib.Path(temp_dir)), \
                mock.patch.object(export_l3.drift, "load_confirmation", return_value=confirmation), \
                mock.patch.object(export_l3, "_confirmation_trust", return_value={
                    "status": "quarantined", "reason": "artifact references quarantined revision",
                }), \
                mock.patch.object(export_l3.provenance, "tokens_at") as tokens_at:
            output = pathlib.Path(temp_dir) / "Example.pivots.json"
            output.write_text("stale", encoding="utf-8")

            status = export_l3.export_pivots("Example")

        self.assertEqual(status["state"], "unavailable")
        self.assertFalse(output.exists())
        tokens_at.assert_not_called()


if __name__ == "__main__":
    unittest.main()

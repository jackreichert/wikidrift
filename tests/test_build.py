"""Characterization tests for the viewer's HTML rendering (viewer/build.py)."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "viewer"))
import build  # noqa: E402


REC = {
    "article": "Testland",
    "qid": "Q42",
    "editions": {
        "en": {
            "present": True,
            "revid": 123,
            "title": "Testland",
            "timestamp": "2020-01-01",
            "prose_chars": 100,
        }
    },
}
ST = {
    "article": "Testland",
    "langs": ["en", "he"],
    "entities": ["Israel"],
    "editions": {
        "en": {
            "lead": {
                "Israel": {
                    "stance": "neutral",
                    "npov_departure": False,
                    "evidence": "x",
                }
            }
        },
        "he": {
            "lead": {
                "Israel": {
                    "stance": "critical",
                    "npov_departure": True,
                    "evidence": "y",
                }
            }
        },
    },
}
DIVER = {
    "static": {
        "Testland": {
            "variants": {
                "lead": {"divergence": 1.0},
                "focal": {"divergence": 1.0},
            }
        }
    },
    "pivot_relative": {},
}


def _article_html():
    f = build.Findings(
        receipts={"Testland": REC},
        stances={"Testland": ST},
        diver=DIVER,
    )
    return build.article_page("Testland", f, categories={"Testland": "Other"})


def _index_html():
    return build.index_page(
        ["Testland"],
        build.Findings(stances={"Testland": ST}, diver=DIVER),
        categories={"Testland": "Other"},
    )


class FindingsDiscovery(unittest.TestCase):
    def test_article_shard_l1_confirmation_adds_analyzed_article(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = pathlib.Path(temp_dir)
            findings_dir = data_dir / "articles" / "Analyzed_Topic" / "findings"
            findings_dir.mkdir(parents=True)
            (findings_dir / "Analyzed_Topic.l1-confirmation.json").write_text(
                json.dumps({"article": "Analyzed Topic", "status": "not_confirmed"}),
                encoding="utf-8",
            )

            with mock.patch.object(build, "FIND", data_dir / "findings"), \
                    mock.patch.object(build, "ARTICLES", data_dir / "articles"), \
                    mock.patch.object(build, "DATA", data_dir / "viewer-data"):
                findings = build.gather()

            self.assertEqual(findings.articles(), ["Analyzed Topic"])
            self.assertEqual(findings.confirmations["Analyzed Topic"]["status"], "not_confirmed")

    def test_unavailable_shard_does_not_publish_alias_page(self):
        findings = build.Findings(confirmations={
            "Old Alias": {"article": "Old Alias", "status": "unavailable"},
        })

        self.assertEqual(findings.articles(), [])

    def test_article_shard_finding_overrides_legacy_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = pathlib.Path(temp_dir)
            legacy_dir = data_dir / "findings"
            shard_dir = data_dir / "articles" / "Analyzed_Topic" / "findings"
            legacy_dir.mkdir(parents=True)
            shard_dir.mkdir(parents=True)
            (legacy_dir / "Analyzed_Topic.lexical.json").write_text(
                json.dumps({"article": "Analyzed Topic", "js_divergence": 0.1}),
                encoding="utf-8",
            )
            (shard_dir / "Analyzed_Topic.lexical.json").write_text(
                json.dumps({"article": "Analyzed Topic", "js_divergence": 0.4}),
                encoding="utf-8",
            )

            with mock.patch.object(build, "FIND", legacy_dir), \
                    mock.patch.object(build, "ARTICLES", data_dir / "articles"), \
                    mock.patch.object(build, "DATA", data_dir / "viewer-data"):
                findings = build.gather()

            self.assertEqual(findings.lexical["Analyzed Topic"]["js_divergence"], 0.4)


class ArticlePageRendering(unittest.TestCase):
    def test_exact_not_confirmed_overrides_stale_coarse_pivot(self):
        findings = build.Findings(
            confirmations={"Testland": {"article": "Testland", "status": "not_confirmed"}},
            pivots={"Testland": {"pivots": [{
                "start": "2020-01-01", "end": "2021-01-01", "pwr_mass": 100,
            }]}},
        )

        out = build.article_page("Testland", findings)

        self.assertIn("No candidate rewrite window was confirmed", out)
        self.assertNotIn("Candidate rewrite window", out)

    def test_confirmed_analysis_renders_exact_episode_summary(self):
        findings = build.Findings(confirmations={"Testland": {
            "article": "Testland",
            "status": "confirmed",
            "confirmed_episodes": [{
                "before_revid": 11,
                "before_timestamp": "2024-01-01T00:00:00Z",
                "after_revid": 12,
                "after_timestamp": "2024-01-01T00:20:00Z",
                "durable_spine_drop": 0.75,
                "pwr_mass": 500,
            }],
        }})

        out = build.article_page("Testland", findings)

        self.assertIn("1 confirmed rewrite episode", out)
        self.assertIn("75.0% durable-spine drop", out)
        self.assertIn("oldid=11", out)
        self.assertIn("oldid=12", out)

    def test_unavailable_confirmation_remains_unavailable_when_article_is_published(self):
        findings = build.Findings(
            confirmations={"Testland": {
                "article": "Testland", "status": "unavailable", "coarse_verdict": "SKIP",
            }},
            lexical={"Testland": {"article": "Testland", "js_divergence": 0.1}},
        )

        out = build.article_page("Testland", findings)

        self.assertIn("Too few snapshots for rewrite analysis", out)
        self.assertNotIn("No candidate rewrite window was confirmed", out)

    def test_profile_discloses_snapshot_horizon(self):
        profile = {
            "horizon": "2026-01-01", "median_age_yrs": 4.2, "pct_recent": 25,
            "recent_years": 3.0, "top10_editor_share": 60, "distinct_editors": 42,
        }

        out = build.profile_line(profile)

        self.assertIn("Snapshot data on this page runs through", out)
        self.assertIn("2026-01-01", out)

    def test_renders_title_and_versions(self):
        out = _article_html()
        self.assertIn("Testland", out)
        self.assertIn("123", out)
        self.assertIn("Q42", out)
        self.assertIn("Versions", out)

    def test_versions_prefer_current_framing_receipts(self):
        legacy = {
            "article": "Testland", "qid": "Q42",
            "editions": {
                "en": {"present": True, "revid": 1, "title": "Testland"},
                "ar": {"present": True, "revid": 2, "title": "اختبار"},
            },
        }
        framing = {
            "mode": "pivot_relative",
            "editions_compared": ["en", "sr", "ko"],
            "snapshots": {
                "before": {
                    "en": {"revid": 11, "title": "Testland", "lead": "before"},
                    "sr": {"revid": 21, "title": "Тест", "lead": "pre"},
                    "ko": {"revid": 31, "title": "테스트", "lead": "pre"},
                },
                "after": {
                    "en": {"revid": 12, "title": "Testland", "lead": "after"},
                    "sr": {"revid": 22, "title": "Тест", "lead": "post"},
                    "ko": {"revid": 32, "title": "테스트", "lead": "post"},
                },
            },
            "divergences": [{"topic": "x", "verdict": "differ"}],
        }
        findings = build.Findings(
            receipts={"Testland": legacy},
            framings={"Testland": framing},
        )

        out = build.article_page("Testland", findings)

        self.assertNotIn('oldid=2"', out)
        self.assertNotIn("اختبار", out)
        for revid in (11, 12, 21, 22, 31, 32):
            self.assertIn(f"oldid={revid}", out)
        self.assertIn("<td><b>sr</b></td><td>before</td>", out)
        self.assertIn("<td><b>ko</b></td><td>after</td>", out)

    def test_static_framing_filters_legacy_receipts_to_current_languages(self):
        legacy = {
            "editions": {
                "en": {"present": True, "revid": 1},
                "ar": {"present": True, "revid": 2},
                "sr": {"present": True, "revid": 3},
            },
        }
        framing = {
            "mode": "static", "editions_compared": ["en", "sr"],
            "divergences": [], "llm_usage": {"calls": 1},
        }

        records = build._version_records(legacy, framing)

        self.assertEqual([lang for lang, _, _ in records], ["en", "sr"])

    def test_failed_or_language_less_framing_keeps_legacy_receipts(self):
        legacy = {
            "editions": {
                "en": {"present": True, "revid": 1},
                "ar": {"present": True, "revid": 2},
            },
        }
        failed = {"error": "provider failed", "editions_compared": ["en", "sr"]}
        no_languages = {"divergences": [{"topic": "x"}]}

        self.assertEqual(len(build._version_records(legacy, failed)), 2)
        self.assertEqual(len(build._version_records(legacy, no_languages)), 2)

    def test_legacy_stance_does_not_render_framing(self):
        out = _article_html()
        self.assertNotIn('data-slug="framing"', out)
        self.assertNotIn("Cross-language stance comparison", out)
        self.assertNotIn("Languages checked: en, he", out)
        self.assertNotIn("language openings treat the topic differently", out)

    def test_new_framing_languages_supersede_disjoint_legacy_stance(self):
        framing = {
            "mode": "pivot_relative",
            "editions_compared": ["en", "sr", "ko"],
            "divergences": [{
                "topic": "nationalism", "verdict": "differ",
                "editions_differ": ["sr", "ko"],
            }],
        }
        findings = build.Findings(
            stances={"Testland": ST},
            framings={"Testland": framing},
            diver=DIVER,
        )

        out = build.article_page("Testland", findings, categories={"Testland": "Other"})

        self.assertNotIn("Cross-language stance comparison", out)
        self.assertNotIn("Languages checked: en, he", out)
        self.assertIn("Cross-language lead comparison", out)
        self.assertIn("Languages compared: en, sr, ko", out)

    def test_new_framing_supersedes_overlapping_legacy_stance(self):
        framing = {
            "mode": "pivot_relative",
            "editions_compared": ["en", "ar", "he", "de"],
            "divergences": [{
                "topic": "mandate", "verdict": "differ",
                "editions_differ": ["de"],
            }],
        }
        findings = build.Findings(
            stances={"Testland": ST},
            framings={"Testland": framing},
            diver=DIVER,
        )

        out = build.article_page("Testland", findings, categories={"Testland": "Other"})

        self.assertNotIn("Cross-language stance comparison", out)
        self.assertNotIn("Languages checked: en, he", out)
        self.assertIn("Cross-language lead comparison", out)
        self.assertIn("Languages compared: en, ar, he, de", out)

    def test_superseded_stance_does_not_create_empty_framing_tab(self):
        unavailable_framing = {
            "mode": "pivot_relative",
            "editions_compared": ["en", "sr", "ko"],
            "divergences": [],
            "error": "provider request failed",
        }
        findings = build.Findings(
            stances={"Testland": ST},
            framings={"Testland": unavailable_framing},
            diver=DIVER,
        )

        layers = dict((name, available) for name, available, _ in build._layer_flags("Testland", findings))
        out = build.article_page("Testland", findings, categories={"Testland": "Other"})

        self.assertFalse(layers["Framing"])
        self.assertNotIn('data-slug="framing"', out)
        self.assertNotIn("Cross-language stance comparison", out)
        self.assertNotIn("Language openings treat the topic differently", out)

    def test_renders_temporal_framing_evidence_and_receipts(self):
        framing = {
            "mode": "candidate_relative",
            "pivot_window": {
                "start": "2020-01-01", "end": "2021-01-01", "pwr_mass": 42000,
                "status": "candidate",
            },
            "editions_compared": ["en", "he"],
            "snapshots": {
                "before": {
                    "en": {"revid": 11}, "he": {"revid": 21},
                },
                "after": {
                    "en": {"revid": 12}, "he": {"revid": 22},
                },
            },
            "divergences": [{
                "topic": "cause", "verdict": "differ", "temporal_read": "english_moved_away",
                "editions_differ": ["en", "he"],
                "en_before": "Earlier account", "en_after": "Later account",
                "other_before": "Stable account", "other_after": "Stable account",
                "evidence_en_before": "English before", "evidence_en_after": "English after",
                "evidence_other_before": "Hebrew before", "evidence_other_after": "Hebrew after",
            }],
        }

        out = build.framing_lite_block(framing)

        self.assertIn("Cross-language lead comparison", out)
        self.assertIn("L1 candidate window", out)
        self.assertIn("English moved away", out)
        self.assertIn("<b>Before:</b> Earlier account", out)
        self.assertIn("oldid=11", out)
        self.assertIn("oldid=22", out)

    def test_temporal_badge_color_uses_the_displayed_temporal_read(self):
        framing = {
            "mode": "pivot_relative",
            "divergences": [
                {
                    "topic": "wording", "verdict": verdict,
                    "temporal_read": "difference_persisted",
                }
                for verdict in ("differ", "absent_other", "agree")
            ],
        }

        out = build.framing_lite_block(framing)

        badge = '<span class="badge v-d">difference persisted</span>'
        self.assertEqual(out.count(badge), 3)
        self.assertNotIn('<span class="badge v-a">difference persisted</span>', out)
        self.assertNotIn('<span class="badge v-i">difference persisted</span>', out)

    def test_renders_confirmed_pivot_relative_label_and_exact_receipts(self):
        framing = {
            "mode": "pivot_relative",
            "pivot_window": {
                "start": "2020-06-01T00:00:00Z", "end": "2020-06-02T00:00:00Z",
                "before_revid": 111, "after_revid": 112, "status": "confirmed",
            },
            "editions_compared": ["en"],
            "snapshots": {
                "before": {"en": {"revid": 111}},
                "after": {"en": {"revid": 112}},
            },
            "divergences": [],
        }

        out = build.framing_lite_block(framing)

        self.assertIn("confirmed rewrite", out)
        self.assertIn("oldid=111", out)
        self.assertIn("oldid=112", out)
        self.assertNotIn("L1 candidate window", out)

    def test_failed_framing_is_unavailable_not_a_no_difference_result(self):
        framing = {
            "error": "provider request failed",
            "summary": "LLM comparison failed; no framing result was produced.",
            "divergences": [],
        }
        findings = build.Findings(framings={"Testland": framing})

        block = build.framing_lite_block(framing)
        flags = {name: available for name, available, _ in build._layer_flags("Testland", findings)}

        self.assertIn("No comparison result is available", block)
        self.assertNotIn("No clear differences", block)
        self.assertFalse(flags["Framing"])

    def test_zero_call_insufficient_framing_is_unavailable(self):
        framing = {
            "summary": "Insufficient matched historical content.",
            "divergences": [],
            "llm_usage": {"calls": 0},
        }

        block = build.framing_lite_block(framing)

        self.assertFalse(build._framing_result_available(framing))
        self.assertIn("No comparison result is available", block)
        self.assertNotIn("No clear differences", block)

    def test_completed_empty_framing_is_a_valid_no_difference_result(self):
        framing = {
            "summary": "The supplied openings are substantively aligned.",
            "divergences": [],
            "llm_usage": {"calls": 1},
        }

        block = build.framing_lite_block(framing)

        self.assertTrue(build._framing_result_available(framing))
        self.assertIn("No clear differences", block)

    def test_index_lists_the_article_with_its_link(self):
        idx = _index_html()
        self.assertIn("Testland", idx)
        self.assertIn('href="article/Testland.html"', idx)
        self.assertIn("Largest rewrite first", idx)

    def test_article_body_does_not_deep_link_glossary(self):
        """Article content should explain itself; nav may still link Reading tips."""
        out = _article_html()
        main = out.split("<main", 1)[1].split("</main>", 1)[0]
        self.assertNotIn("glossary.html#", main)

    def test_missing_rewrite_export_is_unavailable_not_a_negative_finding(self):
        out = build.article_page("Unexported", build.Findings())
        self.assertIn("Rewrite analysis is not available", out)
        self.assertNotIn("None stood out", out)

    def test_completed_l1_scan_without_pivot_is_not_missing_coverage(self):
        findings = build.Findings(lexical={"Testland": {
            "span": "2002-01-01 -> 2004-01-01 (no L1 pivot — whole history)",
            "pivot": None,
            "js_divergence": 0.1,
        }})
        out = build.article_page("Testland", findings)
        self.assertIn("No candidate rewrite window was found", out)
        self.assertIn("L1 rewrite scan ran", out)
        self.assertNotIn("Rewrite analysis is not available", out)

    def test_current_rewrite_status_overrides_stale_lexical_marker(self):
        findings = build.Findings(
            lexical={"Testland": {
                "span": "2002-01-01 -> 2004-01-01 (no L1 pivot — whole history)",
                "pivot": None,
            }},
            rewrite_status={"Testland": "unavailable"},
        )
        out = build.article_page("Testland", findings)
        self.assertIn("Rewrite analysis is not available", out)
        self.assertNotIn("No candidate rewrite window was found", out)

    def test_insufficient_snapshots_explains_why_rewrite_is_unavailable(self):
        findings = build.Findings(rewrite_status={"Testland": {
            "state": "unavailable",
            "reason": "too few snapshots",
        }})
        out = build.article_page("Testland", findings)
        self.assertIn("Too few snapshots for rewrite analysis", out)
        self.assertIn("saved token corpus does not contain enough snapshots", out)
        self.assertNotIn("No rewrite timeline was exported", out)

    def test_coarse_pivot_is_a_candidate_with_pwr_metric(self):
        findings = build.Findings(pivots={"Testland": {"pivots": [{
            "start": "2024-01-01", "end": "2025-01-01", "peak_pct": 42.0,
            "pwr_mass": 120000, "before_text": "old", "after_text": "new",
        }]}})
        out = build.article_page("Testland", findings)
        self.assertIn("Candidate rewrite window", out)
        self.assertIn("42% persistence-weighted loss", out)
        self.assertNotIn("42% of the article changed", out)
        pivot = build.pivot_page("Testland", findings.pivots["Testland"]["pivots"][0], 0)
        self.assertIn("Candidate rewrite", pivot)

    def test_overview_lists_every_candidate_window(self):
        findings = build.Findings(pivots={"Testland": {"pivots": [
            {"start": "2007-01-01", "end": "2008-01-01", "peak_pct": 70.0,
             "pwr_mass": 100, "before_text": "old", "after_text": "new"},
            {"start": "2024-01-01", "end": "2025-01-01", "peak_pct": 68.0,
             "pwr_mass": 1000, "before_text": "old", "after_text": "new"},
        ]}})
        out = build.article_page("Testland", findings)
        self.assertIn("2 candidate windows", out)
        self.assertIn('href="Testland.p0.html"', out)
        self.assertIn('href="Testland.p1.html"', out)
        self.assertIn("2007-01-01 → 2008-01-01", out)
        self.assertIn("2024-01-01 → 2025-01-01", out)
        self.assertNotIn("% of the article rewritten", out)

    def test_manual_diff_is_a_comparison_not_a_detected_large_rewrite(self):
        diff = {
            "before": {"date": "2018-01-01", "text": "old"},
            "after": {"text": "new"},
        }
        out = build.article_page("Testland", build.Findings(diffs={"Testland": diff}))
        self.assertIn("Before-and-after comparison", out)
        self.assertNotIn("A large rewrite shows up", out)

    def test_fact_summary_preserves_agree_differ_contradict_and_insufficient(self):
        factcheck = {"claim": {"adjudication": [
            {"question": "A?", "verdict": "agree", "note": "aligned"},
            {"question": "B?", "verdict": "differ", "note": "extra compatible detail"},
            {"question": "C?", "verdict": "contradict", "note": "incompatible"},
            {"question": "D?", "verdict": "insufficient", "note": "not stated"},
        ]}}
        findings = build.Findings(factchecks={"Testland": {"now": factcheck}})
        out = build.article_page("Testland", findings)
        self.assertIn("1 contradict · 1 compatible difference · 1 agree · 1 not enough", out)
        self.assertNotIn("3 of 4 basic facts", out)

    def test_legacy_stance_divergence_does_not_affect_headline(self):
        aligned = {
            "static": {"Testland": {"variants": {"lead": {"divergence": 0.0}}}},
            "pivot_relative": {},
        }
        findings = build.Findings(stances={"Testland": ST}, diver=aligned)
        out = build.article_page("Testland", findings)
        self.assertNotIn("language openings treat the topic differently", out)
        self.assertNotIn("openings mostly line up", out)


class SiteRouting(unittest.TestCase):
    def test_homepage_is_about(self):
        about = build.simple_page("About", "<h1>About WikiDrift</h1>", "about", path="index.html")
        self.assertIn("About WikiDrift", about)
        self.assertIn('href="findings.html"', about)
        self.assertIn("How it works", about)
        self.assertIn('<a href="index.html" class="active" aria-current="page">About</a>', about)

    def test_about_leads_with_live_tool_and_source_links(self):
        body = build.ABOUT_BODY

        actions_start = body.index('<div class="home-actions">')
        first_section = body[:body.index("<h2>")]

        self.assertIn('<a class="primary-action" href="findings.html">', first_section)
        self.assertIn(
            '<a class="secondary-action" href="https://github.com/jackreichert/wikidrift/">',
            first_section,
        )
        self.assertLess(actions_start, body.index("Wikipedia was briliant idea"))

    def test_editorial_copy_comes_from_templates(self):
        self.assertIn("research lead", build.FINDINGS_BODY)
        self.assertIn('href="summary.html"', build.FINDINGS_BODY)
        summary = build.simple_page(
            "Summary of findings", build.SUMMARY_BODY, None, path="summary.html"
        )
        self.assertIn("Persistence-weighted loss detects durable replacement", summary)
        self.assertNotIn('aria-current="page"', summary)
        self.assertIn('<a class="wiki-link" href="findings.html">Browse all findings', summary)
        page = build.render_page(title="Test", body="<h1>Test</h1>", root="../")
        self.assertIn('href="../findings.html"', page)
        self.assertIn(
            '<span class="project-credit">an <a href="https://encyclopediae.org/">'
            'encyclopediae.org</a> project</span>',
            page,
        )
        self.assertIn('<footer class="site">', page)

    def test_mermaid_runtime_is_loaded_only_for_pages_with_diagrams(self):
        methodology = build.simple_page(
            "How it works", build.METHODOLOGY_BODY, "methodology"
        )
        plain = build.render_page(title="Test", body="<h1>Test</h1>")

        self.assertIn('class="language-mermaid"', methodology)
        self.assertIn("mermaid@11.4.1/dist/mermaid.min.js", methodology)
        self.assertNotIn("mermaid.min.js", plain)

    def test_mermaid_runtime_has_accessible_enlarge_dialog(self):
        runtime = (build.VIEWER / "site.js").read_text(encoding="utf-8")

        self.assertIn('expand.textContent = "Enlarge diagram"', runtime)
        self.assertIn('expand.setAttribute("aria-haspopup", "dialog")', runtime)
        self.assertIn('dialog.setAttribute("aria-labelledby", titleId)', runtime)
        self.assertIn("dialog.showModal()", runtime)
        self.assertIn('if (event.key !== "Escape") return', runtime)
        self.assertIn('dialog.addEventListener("close", restoreDiagram)', runtime)


if __name__ == "__main__":
    unittest.main()

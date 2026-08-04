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
    def test_glossary_defines_rewrite_measurements_and_evidence(self):
        rendered = build._md_asset("glossary")

        self.assertIn('id="persistence-weighted-loss"', rendered)
        self.assertIn('id="durable-spine"', rendered)
        self.assertIn('id="coarse-exact"', rendered)
        self.assertIn('id="redline-receipt"', rendered)
        self.assertIn("Persistence-weighted loss", rendered)
        self.assertIn("absolute weighted amount lost", rendered)
        self.assertIn("percentage-point decline", rendered)
        self.assertIn("single snapshot-to-snapshot interval", rendered)
        self.assertIn("The <strong>coarse scan</strong>", rendered)
        self.assertIn("The <strong>exact check</strong>", rendered)
        self.assertIn("earlier intervals\nare excluded, not treated as negative findings", rendered)
        self.assertIn("The redline supports reading the change", rendered)
        self.assertIn("the receipt supports auditing", rendered)

    def test_every_published_article_explains_and_renders_the_interval_metric(self):
        findings = build.gather()
        invalid_counts = {}
        for article in findings.articles():
            rendered = build.article_page(article, findings)
            counts = {
                "chart": rendered.count('id="drift-profile-title"'),
                "definition": rendered.count('id="durable-spine-title"'),
            }
            if counts != {"chart": 1, "definition": 1}:
                invalid_counts[article] = counts

        self.assertEqual(invalid_counts, {})

    def test_expanded_political_topics_have_a_descriptive_category(self):
        political_topics = [
            "Xi Jinping",
            "Ilhan Omar",
            "Democratic Socialists of America",
            "Socialism",
            "Capitalism",
            "Democratic Party (United States)",
            "Republican Party (United States)",
            "Elizabeth Warren",
        ]

        categories = build.resolve_categories([*political_topics, "Unmoved mover"])

        self.assertEqual(
            {categories[article] for article in political_topics},
            {"Politics & ideology"},
        )
        self.assertEqual(categories["Unmoved mover"], "Other")
        self.assertLessEqual(
            set(build.CATEGORY.values()) | {build.DEFAULT_CATEGORY},
            set(build.CATEGORY_OPTIONS),
        )

        rendered = build.index_page(
            ["Xi Jinping"],
            build.Findings(),
            categories={"Xi Jinping": "Politics & ideology"},
        )
        self.assertIn('data-cat="Politics &amp; ideology"', rendered)
        self.assertIn('>Politics &amp; ideology</button>', rendered)

    def test_explicit_category_overrides_model_assisted_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = pathlib.Path(temp_dir) / "topic_categories.json"
            cache_path.write_text(
                json.dumps({"version": 1, "categories": {"Xi Jinping": "Other"}}),
                encoding="utf-8",
            )

            categories = build.resolve_categories(
                ["Xi Jinping"],
                use_llm=True,
                cache_path=cache_path,
            )

        self.assertEqual(categories["Xi Jinping"], "Politics & ideology")

    def test_category_cache_rejects_unknown_filter_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = pathlib.Path(temp_dir) / "topic_categories.json"
            cache_path.write_text(
                json.dumps({"version": 1, "categories": {"Testland": "Unlisted label"}}),
                encoding="utf-8",
            )

            categories = build._load_category_cache(cache_path)

        self.assertEqual(categories, {})

    def test_stale_shard_confirmation_degrades_to_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = pathlib.Path(temp_dir)
            article_dir = data_dir / "articles" / "Stale_Topic"
            findings_dir = article_dir / "findings"
            findings_dir.mkdir(parents=True)
            con = build.duckdb.connect(str(article_dir / "provenance.duckdb"))
            from wikidrift import provenance
            provenance.ensure_schema(con)
            con.execute("INSERT INTO rsnap VALUES (?,?,?,?,?)", ("Stale Topic", "2026-01-01", 901, 1, 100))
            con.execute("INSERT INTO endpoint_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                "Stale Topic", "current_stable", 901, 901, "2026-01-01",
                "2026-01-01T00:00:00Z", 172800, None, "stable", "[]",
                provenance.STABLE_ENDPOINT_POLICY, "2026-01-03T00:00:00+00:00",
            ))
            con.close()
            (findings_dir / "Stale_Topic.l1-confirmation.json").write_text(json.dumps({
                "article": "Stale Topic",
                "status": "confirmed",
                "thresholds": build.pipeline.config.confirmation_thresholds(),
                "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": 900},
                "confirmed_episodes": [{"before_revid": 1, "after_revid": 2}],
            }), encoding="utf-8")

            with mock.patch.object(build, "FIND", data_dir / "findings"), \
                    mock.patch.object(build, "ARTICLES", data_dir / "articles"), \
                    mock.patch.object(build, "DATA", data_dir / "viewer-data"):
                findings = build.gather()

            confirmation = findings.confirmations["Stale Topic"]
            rendered = build.confirmation_section(confirmation)

        self.assertEqual(confirmation["status"], "unavailable")
        self.assertEqual(confirmation["confirmed_episodes"], [])
        self.assertIn("Rewrite analysis needs refresh", rendered)
        self.assertNotIn("confirmed rewrite episode", rendered)

    def test_unreceipted_shard_confirmation_is_withheld(self):
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

            self.assertEqual(findings.articles(), [])
            self.assertEqual(findings.confirmations["Analyzed Topic"]["status"], "unavailable")
            self.assertEqual(findings.confirmations["Analyzed Topic"]["trust_status"],
                             "legacy_incompatible")
            self.assertEqual(len(findings.trust_report["withheld"]), 1)

    def test_trust_report_counts_and_explains_withheld_artifacts(self):
        report = {
            "published": [{"status": "published"}],
            "withheld": [{
                "article": "Analyzed Topic",
                "artifact_kind": "stance",
                "path": "Analyzed_Topic.stance.json",
                "status": "legacy_incompatible",
                "reason": "stance artifact lacks revision evidence",
            }],
        }

        payload = build.trust_report_payload(report)
        page = build.trust_report_page(report)

        self.assertEqual(payload["counts"]["published"], 1)
        self.assertEqual(payload["counts"]["withheld"], 1)
        self.assertEqual(payload["counts"]["legacy_incompatible"], 1)
        self.assertIn("stance artifact lacks revision evidence", page)

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
                json.dumps({
                    "article": "Analyzed Topic", "js_divergence": 0.1,
                    "interval_source": "snapshot_endpoints",
                    "before": {"rev": 100}, "after": {"rev": 200},
                }),
                encoding="utf-8",
            )
            (shard_dir / "Analyzed_Topic.lexical.json").write_text(
                json.dumps({
                    "article": "Analyzed Topic", "js_divergence": 0.4,
                    "interval_source": "snapshot_endpoints",
                    "before": {"rev": 100}, "after": {"rev": 200},
                }),
                encoding="utf-8",
            )
            from wikidrift import provenance
            con = build.duckdb.connect(str(shard_dir.parent / "provenance.duckdb"))
            provenance.ensure_schema(con)
            con.execute("INSERT INTO endpoint_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                "Analyzed Topic", "current_stable", 200, 200, "2026-01-01",
                "2026-01-01T00:00:00Z", 172800, None, "stable", "[]",
                provenance.STABLE_ENDPOINT_POLICY, "2026-01-03T00:00:00+00:00",
            ))
            con.executemany("INSERT INTO snapshot_integrity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                ("Analyzed Topic", "2025-01-01", 100, "complete", 10, 10, 1000,
                 0.0, None, None, None, None, None, provenance.SNAPSHOT_INTEGRITY_POLICY,
                 "2026-01-03T00:00:00+00:00"),
                ("Analyzed Topic", "2026-01-01", 200, "complete", 10, 10, 1000,
                 0.0, None, None, None, None, None, provenance.SNAPSHOT_INTEGRITY_POLICY,
                 "2026-01-03T00:00:00+00:00"),
            ])
            con.close()

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

    def test_exact_not_confirmed_relabels_legacy_lexical_pivot(self):
        findings = build.Findings(
            confirmations={"Testland": {
                "article": "Testland",
                "status": "not_confirmed",
                "thresholds": {"confirm_drop": 0.2},
                "interval_profile": [
                    {
                        "start": "2023-07-01", "end": "2024-01-01",
                        "size": 900, "pwr_loss": 4.0, "pwr_removed": 300,
                        "mature": False,
                    },
                    {
                        "start": "2024-01-01", "end": "2024-07-01",
                        "size": 4200, "pwr_loss": 69.5, "pwr_removed": 240130,
                        "mature": True,
                    },
                ],
                "evaluated_candidates": [{
                    "candidate_start": "2024-01-01",
                    "candidate_end": "2024-07-01",
                    "candidate_before_revid": 10,
                    "candidate_after_revid": 20,
                    "source": "interval",
                    "pwr_mass": 1200,
                    "peak_pct": 30.0,
                    "exact_before_revid": 11,
                    "exact_before_timestamp": "2024-03-01T00:00:00Z",
                    "exact_after_revid": 12,
                    "exact_after_timestamp": "2024-03-02T00:00:00Z",
                    "durable_spine_drop": 0.1,
                    "decision": "rejected",
                    "rejection_reason": "durable_spine_drop_below_threshold",
                }],
            }},
            lexical={"Testland": {
                "span": "2024-01-01 -> 2024-07-01 (around L1 pivot ~2024-01-01)",
                "pivot": "2024-01-01",
                "before": {"date": "2024-01-01", "tokens": 100},
                "after": {"date": "2024-07-01", "tokens": 110},
            }},
            pivots={"Testland": {"pivots": [{
                "start": "2024-01-01",
                "end": "2024-07-01",
                "peak_pct": 30.0,
                "pwr_mass": 1200,
                "status": "rejected",
                "before_text": "old",
                "after_text": "new",
            }]}},
        )

        out = build.article_page("Testland", findings)

        self.assertIn("No candidate rewrite window was confirmed", out)
        self.assertIn("around L1 candidate date 2024-01-01", out)
        self.assertIn("exact checking did not confirm a durable rewrite", out)
        self.assertNotIn("around L1 pivot", out)
        self.assertIn("Candidates and exact outcomes", out)
        self.assertIn('id="drift-profile-title"', out)
        self.assertIn("69.5%", out)
        self.assertIn("240,130", out)
        self.assertIn('href="../glossary.html#persistence-weighted-loss"', out)
        self.assertIn("Rejected candidate window", out)
        self.assertIn("Excluded: below mature size; not investigated", out)
        self.assertIn("2024-01-01 → 2024-07-01", out)
        self.assertIn("10.0% durable-spine drop", out)
        self.assertIn("below the required 20.0%", out)
        self.assertIn("Rejected", out)
        self.assertIn('href="Testland.p0.html"', out)
        self.assertIn("View redline", out)
        rewrite = build.confirmation_section(
            findings.confirmations["Testland"], findings.pivots["Testland"], "Testland"
        )
        self.assertEqual(rewrite.count("<table"), 1)

    def test_confirmed_analysis_renders_exact_episode_summary(self):
        findings = build.Findings(
            confirmations={"Testland": {
                "article": "Testland",
                "status": "confirmed",
                "interval_profile": [
                    {
                        "start": "2023-07-01",
                        "end": "2024-01-01",
                        "pwr_loss": 31.0,
                        "pwr_removed": 300,
                        "mature": True,
                    },
                    {
                        "start": "2024-01-01",
                        "end": "2024-07-01",
                        "pwr_loss": 42.0,
                        "pwr_removed": 500,
                        "mature": True,
                    },
                ],
                "evaluated_candidates": [{
                    "candidate_start": "2023-07-01",
                    "candidate_end": "2024-07-01",
                    "exact_before_revid": 11,
                    "exact_before_timestamp": "2024-01-01T00:00:00Z",
                    "exact_after_revid": 12,
                    "exact_after_timestamp": "2024-01-01T00:20:00Z",
                    "durable_spine_drop": 0.75,
                    "decision": "confirmed",
                    "peak_pct": 42.0,
                    "pwr_mass": 500,
                }],
                "confirmed_episodes": [{
                    "candidate_start": "2023-07-01",
                    "candidate_end": "2024-07-01",
                    "before_revid": 11,
                    "before_timestamp": "2024-01-01T00:00:00Z",
                    "after_revid": 12,
                    "after_timestamp": "2024-01-01T00:20:00Z",
                    "durable_spine_drop": 0.75,
                    "pwr_mass": 500,
                }],
            }},
            pivots={"Testland": {"pivots": [{
                "start": "2023-07-01",
                "end": "2024-07-01",
                "peak_pct": 42.0,
                "pwr_mass": 500,
                "before_text": "old",
                "after_text": "new",
            }]}},
        )

        out = build.article_page("Testland", findings)

        self.assertIn("1 confirmed ", out)
        self.assertIn('href="../glossary.html#rewrite-episode"', out)
        self.assertIn("75.0% durable-spine drop", out)
        self.assertIn('id="durable-spine-title"', out)
        self.assertIn('href="../glossary.html#durable-spine"', out)
        self.assertIn("more persistent half of the wording", out)
        self.assertIn("whole candidate window", out)
        self.assertIn("dominant step within that window", out)
        self.assertIn("oldid=11", out)
        self.assertIn("oldid=12", out)
        self.assertIn('href="Testland.p0.html"', out)
        self.assertIn("View redline", out)
        self.assertEqual(out.count("Confirmed candidate window"), 2)
        rewrite = build.confirmation_section(
            findings.confirmations["Testland"], findings.pivots["Testland"], "Testland"
        )
        self.assertEqual(rewrite.count("<table"), 1)
        self.assertIn("Candidates and exact outcomes", rewrite)
        self.assertNotIn("Candidates checked exactly", rewrite)
        self.assertIn('aria-labelledby="candidate-outcomes-heading"', rewrite)
        self.assertIn('aria-label="View redline for candidate 2023-07-01 to 2024-07-01"', rewrite)
        self.assertIn('aria-label="Before exact revision: 2024-01-01T00:00:00Z"', rewrite)
        self.assertLess(rewrite.index("Exact outcome"), rewrite.index("Coarse signal"))

    def test_legacy_confirmed_episode_adds_verdict_to_containing_interval(self):
        out = build._interval_profile_chart({
            "status": "confirmed",
            "interval_profile": [{
                "start": "2025-01-01",
                "end": "2026-01-01",
                "pwr_loss": 42.0,
                "pwr_removed": 500,
                "mature": True,
            }],
            "confirmed_episodes": [{
                "candidate_start": "2025-01-01",
                "candidate_end": "2026-01-01",
                "before_revid": 11,
                "after_revid": 12,
            }],
        })

        self.assertIn("Confirmed candidate window", out)

    def test_confirmed_analysis_discloses_horizon_duration_and_neutral_attribution(self):
        findings = build.Findings(confirmations={"Testland": {
            "article": "Testland",
            "status": "confirmed",
            "corpus_horizon": {"snapshot_date": "2026-01-01", "snapshot_revid": 900},
            "confirmed_episodes": [{
                "before_revid": 11,
                "before_timestamp": "2024-01-01T00:00:00Z",
                "after_revid": 12,
                "after_timestamp": "2024-01-01T00:20:00Z",
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
                "process_context": {
                    "semantic_role": "descriptive_process_context",
                    "revision_activity": [{
                        "revision_id": 12, "timestamp": "2024-01-01T00:20:00Z",
                        "account": "Editor A", "section": "History", "comment": "reorganize",
                        "source_url": "https://en.wikipedia.org/w/index.php?title=Testland&oldid=12",
                    }],
                    "revert_relationships": [{
                        "revision_id": 12, "restores_revision_id": 10,
                        "signals": ["sha1_restoration"],
                        "source_url": "https://en.wikipedia.org/w/index.php?title=Testland&oldid=12",
                    }],
                    "talk_activity": [],
                    "page_operations": [{
                        "log_id": 9, "type": "protect", "action": "protect",
                        "timestamp": "2024-01-01T00:15:00Z", "comment": "temporary",
                        "source_url": "https://en.wikipedia.org/w/index.php?title=Special:Log&logid=9",
                    }],
                    "availability": {
                        "talk_activity": {"status": "unavailable", "reason": "retrieval timed out"},
                    },
                },
            }],
        }}, pivots={"Testland": {"pivots": [{
            "before_rev": 11,
            "after_rev": 12,
            "before_text": "old",
            "after_text": "new",
        }]}})

        out = build.article_page("Testland", findings)

        self.assertIn("Snapshot corpus through <b>2026-01-01</b>", out)
        self.assertIn("20 minutes", out)
        self.assertIn('href="Testland.p0.html"', out)
        self.assertIn(
            'aria-label="View redline for exact revisions 11 to 12"',
            out,
        )
        self.assertIn("<b>80</b>", out)
        self.assertIn("<b>40</b> surviving replacement", out)
        self.assertIn('href="../glossary.html#snapshot-mature-token"', out)
        self.assertIn("associated with <b>100.0%</b> of removals", out)
        self.assertIn("origin author of <b>100.0%</b> of surviving replacement text", out)
        self.assertIn("does not establish bias, motive, or misconduct", out)
        self.assertIn("Editorial process context", out)
        self.assertIn("History", out)
        self.assertIn("oldid=12", out)
        self.assertIn("logid=9", out)
        self.assertIn("Talk-page activity unavailable", out)
        self.assertIn("retrieval timed out", out)
        self.assertLess(out.index("Editorial process context"), out.index("Exact-event attribution"))

    def test_confirmation_only_episodes_link_each_matching_redline(self):
        episodes = [
            {
                "before_revid": before,
                "before_timestamp": f"202{index}-01-01T00:00:00Z",
                "after_revid": after,
                "after_timestamp": f"202{index}-01-01T00:10:00Z",
                "durable_spine_drop": 0.5,
                "pwr_mass": 100,
            }
            for index, (before, after) in enumerate(((11, 12), (21, 22), (31, 32)), start=3)
        ]
        pivots = {
            "pivots": [
                {"before_rev": episode["before_revid"], "after_rev": episode["after_revid"]}
                for episode in episodes
            ]
        }

        out = build.confirmation_section(
            {"status": "confirmed", "confirmed_episodes": episodes},
            pivots,
            "Testland",
        )

        self.assertEqual(out.count(">View redline</a>"), 3)
        for index, (before, after) in enumerate(((11, 12), (21, 22), (31, 32))):
            self.assertIn(f'href="Testland.p{index}.html"', out)
            self.assertIn(
                f'aria-label="View redline for exact revisions {before} to {after}"',
                out,
            )

    def test_confirmation_episode_without_matching_pivot_marks_redline_unavailable(self):
        out = build.confirmation_section(
            {
                "status": "confirmed",
                "confirmed_episodes": [{
                    "before_revid": 11,
                    "after_revid": 12,
                    "durable_spine_drop": 0.5,
                }],
            },
            {"pivots": [{"before_rev": 21, "after_rev": 22}]},
            "Testland",
        )

        self.assertIn("Redline unavailable", out)
        self.assertNotIn('href="Testland.p0.html"', out)

    def test_unavailable_confirmation_remains_unavailable_when_article_is_published(self):
        findings = build.Findings(
            confirmations={"Testland": {
                "article": "Testland", "status": "unavailable", "coarse_verdict": "SKIP",
            }},
            lexical={"Testland": {"article": "Testland", "js_divergence": 0.1}},
        )

        out = build.article_page("Testland", findings)

        self.assertIn("Too few snapshots for rewrite analysis", out)
        self.assertIn('id="drift-profile-title"', out)
        self.assertIn("How the detector reached this state", out)
        self.assertIn("Data missing", out)
        self.assertIn("Not enough snapshots", out)
        self.assertIn("Not scored", out)
        self.assertIn("Too few snapshots were available", out)
        self.assertNotIn("Legacy receipt", out)
        self.assertNotIn("No candidate rewrite window was confirmed", out)
        self.assertIn('class="drift-axis"', out)
        self.assertIn('class="drift-row drift-row-missing"', out)
        self.assertIn("25% candidate floor", out)

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

    def test_rewrite_terms_link_to_accessible_glossary_tooltips(self):
        rendered = build.article_page("Unexported", build.Findings())

        self.assertIn('class="glossary-term"', rendered)
        self.assertIn('href="../glossary.html#durable-spine"', rendered)
        self.assertIn('href="../glossary.html#persistence-weighted-loss"', rendered)
        self.assertIn('data-tooltip="', rendered)
        self.assertRegex(rendered, r'aria-describedby="glossary-description-\d+"')
        self.assertRegex(rendered, r'<span class="sr-only" id="glossary-description-\d+">')

    def test_rewrite_fallbacks_start_with_section_heading_before_metric_definition(self):
        pivots = build.Findings(pivots={"Testland": {"pivots": []}})
        pivots.pivots["Testland"]["pivots"].append({
            "start": "2020-01-01",
            "end": "2021-01-01",
            "peak_pct": 30.0,
            "before_text": "old",
            "after_text": "new",
        })
        diff = build.Findings(diffs={"Testland": {
            "before": {"date": "2020-01-01", "text": "old"},
            "after": {"date": "2021-01-01", "text": "new"},
        }})

        for rendered in (
            build.article_page("Testland", pivots),
            build.article_page("Testland", diff),
        ):
            rewrite = rendered.split('id="panel-diff"', 1)[1]
            self.assertLess(rewrite.index("<h2>"), rewrite.index("<h3"))

    def test_missing_rewrite_export_is_unavailable_not_a_negative_finding(self):
        out = build.article_page("Unexported", build.Findings())
        self.assertIn("Rewrite analysis is not available", out)
        self.assertIn('id="durable-spine-title"', out)
        self.assertIn('id="drift-profile-title"', out)
        self.assertIn("How the detector reached this state", out)
        self.assertIn("Data missing", out)
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
        self.assertIn('id="drift-profile-title"', out)
        self.assertIn("No candidate signal", out)
        self.assertIn("Not needed", out)
        self.assertIn("Data missing", out)
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

    def test_partial_confirmation_explains_source_coverage_gap(self):
        findings = build.Findings(confirmations={"Testland": {
            "status": "unavailable",
            "coarse_verdict": "UNAVAILABLE",
            "reason": "loaded 32 of 42 expected snapshots",
        }})

        out = build.article_page("Testland", findings)

        self.assertIn("Rewrite analysis has incomplete source coverage", out)
        self.assertIn("loaded 32 of 42 expected snapshots", out)
        self.assertIn("withholds the result", out)
        self.assertNotIn("Rewrite analysis is not available", out)

    def test_partial_skip_retains_source_gap_copy_and_descriptive_stage(self):
        findings = build.Findings(confirmations={"Testland": {
            "status": "unavailable",
            "coarse_verdict": "SKIP",
            "source_state": {
                "source_status": "partial",
                "reason": "loaded 32 of 42 expected snapshots",
            },
            "interval_profile": [{
                "start": "2012-07-01", "end": "2018-01-01", "pwr_loss": 9.42,
                "pwr_removed": 1412, "mature": False, "eligible": False,
            }],
        }})

        out = build.article_page("Testland", findings)

        self.assertIn("Rewrite analysis has incomplete source coverage", out)
        self.assertIn("loaded 32 of 42 expected snapshots", out)
        self.assertIn("No mature covered intervals", out)
        self.assertIn("Readable snapshots were scored descriptively", out)
        self.assertNotIn("Too few snapshots for rewrite analysis", out)

    def test_interval_profile_marks_gap_spanning_interval_as_coverage_excluded(self):
        chart = build._interval_profile_chart({
            "coarse_verdict": "SKIP",
            "status": "unavailable",
            "interval_profile": [{
                "start": "2012-07-01", "end": "2018-01-01", "pwr_loss": 9.42,
                "pwr_removed": 1412, "mature": True, "eligible": False,
            }],
        })

        self.assertIn("Excluded: missing source coverage", chart)
        self.assertIn("coverage gap", chart)
        self.assertNotIn("Measured: not investigated", chart)

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
        self.assertIn("Candidate redline", pivot)

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
        self.assertIn("<h2>Politics &amp; ideology</h2>", summary)
        self.assertIn("seven-topic browsing category", summary)
        self.assertIn('href="article/Unmoved_mover.html">Unmoved mover</a>', summary)
        self.assertIn("remains outside this category", summary)
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

    def test_summary_unlinks_articles_withheld_from_publication(self):
        body = (
            '<p><a href="article/Published_Topic.html">Published topic</a> and '
            '<a href="article/Withheld_Topic_%28test%29.html">Withheld topic</a>, '
            '<a class="wiki-link" href="article/Other_Withheld.html#lead">its lead</a>, and '
            '<a href="article/Published_Topic.html?view=history">published history</a>.</p>'
        )

        rendered = build._unlink_unpublished_article_links(body, ["Published Topic"])

        self.assertIn('<a href="article/Published_Topic.html">Published topic</a>', rendered)
        self.assertIn("Withheld topic, its lead", rendered)
        self.assertIn(
            '<a href="article/Published_Topic.html?view=history">published history</a>', rendered
        )
        self.assertNotIn("Withheld_Topic", rendered)
        self.assertNotIn("Other_Withheld", rendered)

    def test_mermaid_runtime_is_loaded_only_for_pages_with_diagrams(self):
        methodology = build.simple_page(
            "How it works", build.METHODOLOGY_BODY, "methodology"
        )
        plain = build.render_page(title="Test", body="<h1>Test</h1>")

        self.assertIn('class="language-mermaid"', methodology)
        self.assertIn("mermaid@11.4.1/dist/mermaid.min.js", methodology)
        self.assertNotIn("mermaid.min.js", plain)

    def test_methodology_defines_durable_spine_drop(self):
        methodology = build.simple_page(
            "How it works", build.METHODOLOGY_BODY, "methodology"
        )

        self.assertIn("more persistent starting half", methodology)
        self.assertIn("whole candidate window", methodology)
        self.assertIn("dominant step", methodology)

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

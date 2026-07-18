"""Unit tests for pure engine functions (no network, no DB, no LLM)."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

from wikidrift import l5_factcheck as fc
from wikidrift import l5_crosslingual as xl
from wikidrift import mscore
from wikidrift import l4
from wikidrift import l5_sources as src
from wikidrift import lexical
from wikidrift import drift
from wikidrift import benchmark
from wikidrift import stance
from wikidrift import pipeline
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

    # _classify — the honesty gate: retrofit-lead vs born-in-contested vs demoted vs healthy.
    def test_classify_retrofit_lead(self):
        r = {"verdict": "PIVOT?", "top_mass": l4.MASS_FLOOR + 1, "age_at_pivot": l4.MATURE_PRIOR_YEARS + 1}
        self.assertEqual(l4._classify(r), "retrofit-lead")

    def test_classify_born_in_contested_when_prior_too_young(self):
        r = {"verdict": "PIVOT?", "top_mass": l4.MASS_FLOOR + 1, "age_at_pivot": l4.MATURE_PRIOR_YEARS - 1}
        self.assertEqual(l4._classify(r), "born-in-contested")

    def test_classify_demoted_when_below_mass_floor(self):
        r = {"verdict": "PIVOT?", "top_mass": l4.MASS_FLOOR - 1, "age_at_pivot": 99}
        self.assertEqual(l4._classify(r), "demoted")

    def test_classify_missing_age_defaults_to_born_in_contested(self):
        # a PIVOT? over the mass floor but with no measured prior ⇒ conservative (not a retrofit claim)
        self.assertEqual(l4._classify({"verdict": "PIVOT?", "top_mass": l4.MASS_FLOOR + 1}), "born-in-contested")

    def test_classify_non_pivot_verdicts(self):
        self.assertEqual(l4._classify({"verdict": "HEALTHY"}), "healthy")
        self.assertEqual(l4._classify({"verdict": "INSUFFICIENT"}), "insufficient")


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

    def test_annotate_ranks_by_pwr_mass_and_sets_age(self):
        eps = [{"start": ("2020-01-01", 1), "end": ("2020-07-01", 2), "abs": 100, "peak": 20},
               {"start": ("2024-01-01", 3), "end": ("2024-07-01", 4), "abs": 500, "peak": 30}]
        out = drift.annotate_episodes(eps, "2026-01-01")
        self.assertEqual([e["abs"] for e in out], [500, 100])    # ranked by PWR-mass, age-agnostic
        self.assertGreater(out[1]["age"], out[0]["age"])         # the older episode carries the larger age


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


class SlowBleedWindow(unittest.TestCase):
    """_cumulative_loss_windows: rolling 12-month cumulative PWR-loss detector."""
    # series row: (d0, r0, d1, r1, ratio%, size, wlost)

    def _make_series(self, intervals):
        """Build a minimal series from (d0, d1, size, wlost) tuples."""
        return [(d0, 0, d1, 0, 100.0 * wlost / size if size else 0, size, wlost)
                for d0, d1, size, wlost in intervals]

    def test_returns_none_for_empty_series(self):
        self.assertIsNone(drift._cumulative_loss_windows([]))

    def test_returns_none_when_below_threshold(self):
        # 4 quarters, each losing 20 tokens of 1000 → ratio 0.08 < SLOW_BLEED_FLOOR
        series = self._make_series([
            ("2022-01-01", "2022-04-01", 1000, 20),
            ("2022-04-01", "2022-07-01", 1000, 20),
            ("2022-07-01", "2022-10-01", 1000, 20),
            ("2022-10-01", "2023-01-01", 1000, 20),
        ])
        self.assertIsNone(drift._cumulative_loss_windows(series))

    def test_detects_slow_bleed_above_threshold(self):
        # 4 intervals in one year, each removing 100 of 1000 tokens → ratio 0.4 ≥ SLOW_BLEED_FLOOR
        series = self._make_series([
            ("2022-01-01", "2022-04-01", 1000, 100),
            ("2022-04-01", "2022-07-01", 1000, 100),
            ("2022-07-01", "2022-10-01", 1000, 100),
            ("2022-10-01", "2023-01-01", 1000, 100),
        ])
        result = drift._cumulative_loss_windows(series)
        self.assertIsNotNone(result)
        start, end, ratio = result
        self.assertGreaterEqual(ratio, drift.SLOW_BLEED_FLOOR)

    def test_window_does_not_span_more_than_twelve_months(self):
        # bleed spread over 18 months → should NOT trigger if no single 12-month window accumulates enough
        series = self._make_series([
            ("2021-01-01", "2021-07-01", 1000, 100),   # 6 months apart
            ("2021-07-01", "2022-01-01", 1000, 100),   # another 6 (total = 12 months, ratio=0.2 < 0.35)
            ("2022-01-01", "2022-07-01", 1000, 100),   # slides past first interval
        ])
        # Each 12-month window contains at most 2 intervals (200/1000=0.2) — below threshold
        self.assertIsNone(drift._cumulative_loss_windows(series))


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
                  "lexical": {"js_divergence": 0.08}, "mscore": None}
        self.assertIn("lexical_drift", pipeline._corroboration(result)["signals"])

    def test_lexical_drift_does_not_fire_below_threshold(self):
        result = {"l1": "HEALTHY", "l2_adjudicated": False,
                  "lexical": {"js_divergence": 0.03}, "mscore": None}
        self.assertNotIn("lexical_drift", pipeline._corroboration(result)["signals"])

    def test_count_matches_signals_length(self):
        result = {"l1": "PIVOT?", "l2_adjudicated": True,
                  "l2": {"shifts": {"Testland": {"start": -1, "end": 1, "shifted": True, "n": 2}}},
                  "lexical": {"js_divergence": 0.09}, "mscore": None}
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


if __name__ == "__main__":
    unittest.main()

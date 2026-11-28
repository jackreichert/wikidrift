"""Executable acceptance tests for tests/features/topic_review.feature.

Every Scenario in the feature file is bound, by exact name, to one test method through the
``@scenario`` decorator, and a meta-test fails when a Scenario has no test or a test has no
Scenario. Examples tables are read from the feature file, so the table and the test cannot drift.

Scenarios tagged ``@bug`` describe intended behavior that the current site does not meet. They run
to completion; an AssertionError is reported as a skip that names the open bug, and a pass is a
failure ("remove the @bug tag"). Any other exception is a real test error and is never hidden.

All fixtures are synthetic. Nothing here reads the token corpus, the network or an LLM.
"""
import contextlib
import html as html_lib
import io
import pathlib
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "viewer"))
import build  # noqa: E402

FEATURE = ROOT / "tests" / "features" / "topic_review.feature"
T = "Testland"

SMELLS = (
    "confirmed_without_pivots",
    "framing_langs_mismatch",
    "framing_claims_confirmed_while_rewrite_withheld",
    "framing_mode_mismatch",
    "withheld_copy_mismatch",
)


# ---------------------------------------------------------------------------
# Feature file binding
# ---------------------------------------------------------------------------
def parse_feature(path):
    """Return {scenario name: {"tags": set, "examples": [dict, ...]}} from a Gherkin file."""
    scenarios = {}
    feature_tags, rule_tags, pending = set(), set(), []
    current = None
    in_examples, header = False, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            pending.extend(line.split())
            continue
        match = re.match(r"(Feature|Rule|Scenario Outline|Scenario):\s*(.*)", line)
        if match:
            kind, name = match.groups()
            in_examples, header = False, None
            if kind == "Feature":
                feature_tags, pending = set(pending), []
            elif kind == "Rule":
                rule_tags, pending = feature_tags | set(pending), []
            else:
                if name in scenarios:
                    raise AssertionError(f"duplicate scenario name: {name}")
                current = {"tags": (rule_tags or feature_tags) | set(pending), "examples": []}
                scenarios[name] = current
                pending = []
            continue
        if line.startswith("Examples:"):
            in_examples, header = True, None
            continue
        if in_examples and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if header is None:
                header = cells
            else:
                current["examples"].append(dict(zip(header, cells)))
    return scenarios


SCENARIOS = parse_feature(FEATURE)
BOUND = {}


def examples(name):
    return SCENARIOS[name]["examples"]


def scenario(name):
    """Bind a test method to a Scenario; @bug scenarios assert the intended, currently unmet behavior."""
    if name not in SCENARIOS:
        raise AssertionError(f"no scenario named {name!r} in {FEATURE.name}")

    def decorate(test):
        if name in BOUND:
            raise AssertionError(f"scenario {name!r} bound twice")
        BOUND[name] = test.__name__
        if "@bug" not in SCENARIOS[name]["tags"]:
            return test

        def known_bug(self):
            try:
                test(self)
            except AssertionError as exc:
                first = str(exc).strip().splitlines()[0] if str(exc).strip() else "assertion failed"
                self.skipTest(f"known bug (@bug): {first[:160]}")
            self.fail(f"@bug scenario now passes; remove the @bug tag from {name!r}")

        known_bug.__name__ = test.__name__
        known_bug.__doc__ = test.__doc__
        return known_bug

    return decorate


def each_example(name, check):
    """Run check(row) for every Examples row and report all failing rows together."""
    failures = []
    for row in examples(name):
        try:
            check(row)
        except AssertionError as exc:
            failures.append(f"{row}: {exc}")
    if failures:
        raise AssertionError("\n".join(failures))


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
def window(i):
    return f"{2010 + 2 * i}-01-01", f"{2011 + 2 * i}-01-01"


def episode(i, **over):
    start, end = window(i)
    row = {
        "candidate_start": start, "candidate_end": end,
        "before_revid": 1000 + 10 * i + 1, "after_revid": 1000 + 10 * i + 2,
        "before_timestamp": f"{2010 + 2 * i}-06-01T00:00:00Z",
        "after_timestamp": f"{2010 + 2 * i}-06-01T00:20:00Z",
        "durable_spine_drop": 0.5, "pwr_mass": 1000, "duration_seconds": 1200,
    }
    row.update(over)
    return row


def candidate(i, decision="confirmed", **over):
    ep = episode(i)
    row = {
        "candidate_start": ep["candidate_start"], "candidate_end": ep["candidate_end"],
        "exact_before_revid": ep["before_revid"], "exact_after_revid": ep["after_revid"],
        "decision": decision, "durable_spine_drop": 0.5, "peak_pct": 40.0, "pwr_mass": 1000,
        "exact_pair_drop": 0.45,
    }
    if decision == "rejected":
        row.update(durable_spine_drop=0.1, rejection_reason="durable_spine_drop_below_threshold")
    row.update(over)
    return row


def interval(i=0, loss=40.0, **over):
    start, end = window(i)
    row = {
        "start": start, "end": end, "size": 5000, "pwr_loss": loss, "pwr_removed": 1000 * (i + 1),
        "pwr_gain": 5.0, "pwr_added": 200, "confirmable": True,
    }
    if loss >= 15.0:
        row["anomaly_types"] = ["loss"]
    row.update(over)
    return row


def confirmation(n=2, **over):
    row = {
        "article": T, "status": "confirmed", "coarse_verdict": "PIVOT?",
        "thresholds": {"confirm_drop": 0.2},
        "interval_profile": [interval(i) for i in range(n)],
        "evaluated_candidates": [candidate(i) for i in range(n)],
        "confirmed_episodes": [episode(i) for i in range(n)],
    }
    row.update(over)
    return row


def rejected_confirmation(**over):
    row = confirmation(
        1, status="not_confirmed", confirmed_episodes=[],
        evaluated_candidates=[candidate(0, "rejected")],
    )
    row.update(over)
    return row


def healthy_confirmation(**over):
    row = {
        "article": T, "status": "not_confirmed", "coarse_verdict": "HEALTHY",
        "thresholds": {"confirm_drop": 0.2},
        "interval_profile": [interval(0, 3.0, pwr_gain=1.0)],
        "evaluated_candidates": [], "confirmed_episodes": [],
    }
    row.update(over)
    return row


def withheld(reason="stale exact confirmation", n=2, **over):
    """Mirror build._load_confirmations: a withheld file keeps intervals and candidates, not episodes."""
    return confirmation(n, status="unavailable", reason=reason, confirmed_episodes=[], **over)


def pivot(i, status="confirmed", **over):
    start, end = window(i)
    row = {
        "start": start, "end": end, "before_rev": 1000 + 10 * i + 1, "after_rev": 1000 + 10 * i + 2,
        "peak_pct": 40.0, "pwr_mass": 1000, "before_text": f"old text {i}", "after_text": f"new text {i}",
    }
    if status:
        row["status"] = status
    row.update(over)
    return row


def pivots(n=2, indexes=None, **over):
    return {"article": T, "pivots": [pivot(i, **over) for i in (indexes if indexes is not None else range(n))]}


def divergence(i=0, verdict="differ"):
    return {
        "topic": f"topic {i}", "verdict": verdict, "temporal_read": "difference_persisted",
        "editions_differ": ["fr"], "en_before": "a", "en_after": "b", "other_before": "c",
        "other_after": "d", "en_says": "en text", "other_says": "other text",
    }


def framing(editions=("en", "fr", "de"), mode="pivot_relative", win=("2010-01-01", "2011-01-01"),
            win_status="confirmed", rows=1, contradict=0, calls=1, snapshots=False, **over):
    rec = {
        "article": T, "editions_compared": list(editions), "summary": "Openings compared.",
        "llm_usage": {"calls": calls},
        "divergences": [
            divergence(i, "contradict" if i < contradict else "differ") for i in range(rows)
        ],
    }
    if mode:
        rec["mode"] = mode
    if win:
        rec["pivot_window"] = {"start": win[0], "end": win[1], "status": win_status}
    if snapshots:
        rec["snapshots"] = {
            phase: {lang: {"revid": 5000 + 10 * k + (0 if phase == "before" else 1), "timestamp": "2020-01-01"}
                    for k, lang in enumerate(editions)}
            for phase in ("before", "after")
        }
    rec.update(over)
    return rec


def framing_event(i, editions):
    start, end = window(i)
    return {
        "analysis_status": "available", "episode_window": {
            "start": start, "end": end, "before_revid": 7000 + i * 10, "after_revid": 7001 + i * 10,
        },
        "mode": "pivot_relative", "pivot_window": {"start": start, "end": end, "status": "confirmed"},
        "editions_compared": list(editions), "llm_usage": {"calls": 1},
        "divergences": [divergence(i)], "summary": f"event {i}",
    }


RECEIPTS = {
    "article": T, "qid": "Q42",
    "editions": {
        lang: {"present": True, "revid": 9000 + k, "title": T, "timestamp": "2020-01-01", "prose_chars": 100}
        for k, lang in enumerate(("en", "fr", "de", "he"))
    },
}

SOURCES = {
    "span": "2020 to 2024", "before": {"refs": 10, "n_domains": 5}, "after": {"refs": 12, "n_domains": 7},
    "added": [{"domain": f"added{i}.org", "from": 0, "to": i + 1} for i in range(3)],
    "dropped": [{"domain": "dropped0.org", "from": 3, "to": 0}],
}

LEXICAL_EVENT = {
    "analysis_status": "available",
    "episode_window": {"before_revid": 1, "after_revid": 2, "start": "2020-01-01", "end": "2020-02-01",
                       "before_timestamp": "2020-01-01", "after_timestamp": "2020-02-01"},
    "js_divergence": 0.5, "before": {"tokens": 10}, "after": {"tokens": 12},
}


def adjudication(verdicts):
    return {"now": {"claim": {"adjudication": [
        {"question": f"Question {i}?", "verdict": v, "note": f"note {i}"} for i, v in enumerate(verdicts)
    ]}}}


def full_findings():
    return dict(
        confirmations={T: confirmation(2)}, pivots={T: pivots(2)},
        rewrite_status={T: {"state": "finding"}},
        lexical={T: {"article": T, "js_divergence": 0.3, "span": "2010 to 2012",
                     "before": {"date": "2010-01-01", "tokens": 100},
                     "after": {"date": "2012-01-01", "tokens": 110}}},
        sources={T: SOURCES},
        factchecks={T: adjudication(["agree", "differ", "contradict"])},
        framings={T: framing(snapshots=True, win=window(0))},
        receipts={T: RECEIPTS},
    )


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------
_INLINE = re.compile(r"</?(?:a|b|i|em|strong|span|small|code|abbr)\b[^>]*>")
_SR_ONLY = re.compile(r'<span class="sr-only"[^>]*>.*?</span>', re.S)


def text_of(markup):
    """Visible text: tooltips and screen-reader duplicates removed, inline tags joined, blocks spaced."""
    markup = _SR_ONLY.sub("", markup or "")
    markup = _INLINE.sub("", markup)
    markup = re.sub(r"<[^>]+>", " ", markup)
    return re.sub(r"\s+", " ", html_lib.unescape(markup)).strip()


class Page:
    def __init__(self, markup):
        self.html = markup
        starts = [(m.start(), m.group(1)) for m in re.finditer(
            r'<section class="panel[^"]*" role="tabpanel" id="panel-([a-z]+)"', markup)]
        self.panels = {}
        for index, (start, slug) in enumerate(starts):
            stop = starts[index + 1][0] if index + 1 < len(starts) else len(markup)
            self.panels[slug] = markup[start:stop]
        self.tabs = re.findall(r'<button class="tab[^"]*" role="tab"[^>]*>([^<]*)</button>', markup)

    def panel(self, slug):
        return self.panels.get(slug, "")

    def text(self, slug=None):
        return text_of(self.html if slug is None else self.panel(slug))

    def heading(self, slug):
        match = re.search(r"<h2[^>]*>(.*?)</h2>", self.panel(slug), re.S)
        return text_of(match.group(1)) if match else ""

    @property
    def summary(self):
        match = re.search(r'<p class="summary">(.*?)</p>', self.html, re.S)
        return text_of(match.group(1)) if match else ""

    def signals(self):
        return [
            (text_of(label), text_of(value)) for label, value in re.findall(
                r'<div class="signal-label">(.*?)</div><div class="signal-value">(.*?)</div>',
                self.panel("overview"), re.S)
        ]

    def signal(self, label_part):
        for label, value in self.signals():
            if label_part.lower() in label.lower():
                return value
        return None

    @property
    def rewrite_signal(self):
        signals = self.signals()
        return signals[0][1] if signals else None

    def redline_links(self):
        return re.findall(r'href="([^"]*\.p\d+\.html)"', self.html)

    def rows(self):
        return re.findall(r'<li class="drift-row[^"]*">.*?</li>', self.html, re.S)

    def coverage(self):
        have = re.search(r"Available evidence:</b>(.*?)</p>", self.panel("overview"), re.S)
        missing = re.search(r"Not available in this export:</b>(.*?)</p>", self.panel("overview"), re.S)
        return (
            [text_of(x) for x in re.findall(r"<b>(.*?)</b>", have.group(1))] if have else [],
            [x.strip() for x in text_of(missing.group(1)).split(". Missing")[0].split(",")] if missing else [],
        )


def render(**kw):
    return Page(build.article_page(T, build.Findings(**kw)))


def build_site(findings):
    """Run the real site build against synthetic findings into a temporary directory."""
    stack = contextlib.ExitStack()
    site = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
    stack.enter_context(mock.patch.object(build, "SITE", site))
    stack.enter_context(mock.patch.object(build, "gather", return_value=findings))
    with contextlib.redirect_stdout(io.StringIO()):
        build.main(["--category-cache", str(site / "categories.json")])
    return stack, site


@contextlib.contextmanager
def built_site(**kw):
    stack, site = build_site(build.Findings(**kw))
    with stack:
        yield site


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.hrefs = set(), []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if attrs.get("name"):
            self.ids.add(attrs["name"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.hrefs.append(attrs[key])


def parse_links(path):
    parser = _Links()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def dead_links(site, files):
    problems, cache = [], {}

    def ids_of(path):
        if path not in cache:
            cache[path] = parse_links(path).ids
        return cache[path]

    for page in files:
        for href in parse_links(page).hrefs:
            if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", href, re.I):
                continue
            target, _, fragment = href.partition("#")
            path = page if not target else (page.parent / target).resolve()
            if not path.exists():
                problems.append(f"{page.name}: {href} -> missing file")
            elif fragment and path.suffix == ".html" and not (
                fragment in ids_of(path) or f"panel-{fragment}" in ids_of(path)
            ):
                problems.append(f"{page.name}: {href} -> missing id")
    return problems


# ---------------------------------------------------------------------------
# Meta: the feature file and the tests stay in lockstep
# ---------------------------------------------------------------------------
class FeatureBinding(unittest.TestCase):
    def test_every_scenario_has_exactly_one_test(self):
        unbound = sorted(set(SCENARIOS) - set(BOUND))
        self.assertEqual([], unbound, "scenarios without a test")

    def test_bug_scenarios_are_tagged_and_the_smell_taxonomy_is_covered(self):
        tags = set().union(*(entry["tags"] for entry in SCENARIOS.values()))
        for smell in SMELLS:
            self.assertIn(f"@smell_{smell}", tags, f"no scenario carries @smell_{smell}")
        self.assertTrue(any("@bug" in entry["tags"] for entry in SCENARIOS.values()))

    def test_every_scenario_names_a_rule_level_policy_or_bug_state(self):
        for name, entry in SCENARIOS.items():
            self.assertTrue({"@implemented", "@policy"} & entry["tags"], name)


# ---------------------------------------------------------------------------
# Rule 1: the Rewrite tab agrees with confirmation and rewrite_status data
# ---------------------------------------------------------------------------
REWRITE_DATA = {
    "an exact confirmation with 2 confirmed episodes":
        (lambda: dict(confirmations={T: confirmation(2)}, pivots={T: pivots(2)}), True),
    "an exact confirmation that rejected its candidate":
        (lambda: dict(confirmations={T: rejected_confirmation()}), False),
    "a stale exact confirmation":
        (lambda: dict(confirmations={T: withheld("stale exact confirmation")}), False),
    "an exact confirmation withheld for a missing endpoint":
        (lambda: dict(confirmations={T: withheld("artifact withheld: stable endpoint receipt is missing")}), False),
    "a confirmation with too few snapshots":
        (lambda: dict(confirmations={T: {
            "article": T, "status": "unavailable", "coarse_verdict": "SKIP", "reason": "too few snapshots",
        }}), False),
    "no confirmation and rewrite_status none":
        (lambda: dict(rewrite_status={T: {"state": "none"}}), False),
    "no confirmation and no rewrite_status":
        (lambda: {}, False),
}


class RewriteTabAgreesWithData(unittest.TestCase):
    @scenario("The recorded rewrite state decides the Rewrite heading and the Overview signal")
    def test_state_decides_heading_and_signal(self):
        def check(row):
            build_kwargs, confirmed = REWRITE_DATA[row["data"]]
            page = render(**build_kwargs())
            self.assertEqual(row["heading"], page.heading("diff"))
            self.assertEqual(row["signal"], page.rewrite_signal)
            if not confirmed:
                self.assertNotIn("confirmed rewrite", page.summary.lower())
                self.assertNotRegex(page.summary, r"\d+ confirmed")
        each_example("The recorded rewrite state decides the Rewrite heading and the Overview signal", check)

    @scenario("Confirmed episode counts agree everywhere on the page")
    def test_confirmed_counts_agree(self):
        page = render(confirmations={T: confirmation(3)}, pivots={T: pivots(3)})
        self.assertIn("3 confirmed rewrite episodes", page.summary)
        self.assertEqual("3 confirmed", page.rewrite_signal)
        self.assertEqual("3 confirmed rewrite episodes", page.heading("diff"))
        outcomes = re.findall(r'<span class="candidate-decision">(.*?)</span>', page.panel("diff"))
        self.assertEqual(3, len(outcomes))
        self.assertTrue(all(o.startswith("Confirmed") for o in outcomes), outcomes)
        self.assertEqual(3, page.panel("diff").count(">View redline</a>"))

    @scenario("Rejected candidates are never presented as confirmed")
    def test_rejected_never_confirmed(self):
        page = render(confirmations={T: rejected_confirmation()})
        diff = page.text("diff")
        self.assertIn("2010-01-01 → 2011-01-01", diff)
        self.assertIn("Rejected: 10.0% durable-spine drop, below the required 20.0%", diff)
        self.assertIn("Rejected candidate window", diff)
        for where, content in (("page", page.text()),):
            self.assertNotRegex(content, r"\bConfirmed\b", where)
        self.assertNotRegex(page.summary + page.text("overview"), r"(?i)confirmed rewrite")

    @scenario("Mixed exact outcomes are counted from the candidate data")
    def test_mixed_outcomes(self):
        page = render(confirmations={T: confirmation(
            1, evaluated_candidates=[candidate(0), candidate(1, "rejected"), candidate(2, "rejected")],
        )})
        self.assertIn("1 confirmed, 2 rejected", page.text("diff"))
        self.assertEqual("1 confirmed rewrite episode", page.heading("diff"))
        self.assertIn("1 confirmed rewrite episode", page.summary)

    @scenario("A healthy coarse scan is not described as a rejected candidate")
    def test_healthy_scan_is_not_a_rejection(self):
        page = render(confirmations={T: healthy_confirmation()})
        self.assertEqual("No candidate rewrite window was found", page.heading("diff"))
        self.assertEqual("No candidate window found", page.rewrite_signal)
        combined = page.text("diff") + " " + page.text("overview")
        self.assertNotIn("exact checking did not confirm", combined)

    @scenario("rewrite_status unavailable outranks a confirmation file that still says confirmed")
    def test_status_unavailable_outranks_confirmed_file(self):
        page = render(
            confirmations={T: confirmation(2)}, pivots={T: pivots(2)},
            rewrite_status={T: {"state": "unavailable", "reason": "stale exact confirmation"}},
        )
        self.assertEqual("Rewrite analysis needs refresh", page.heading("diff"))
        self.assertEqual([], page.redline_links())
        self.assertNotIn("View redline", page.html)
        self.assertNotRegex(page.summary + page.text("overview"), r"(?i)\d+ confirmed")

    @scenario("rewrite_status unavailable outranks leftover redline pivots")
    def test_status_unavailable_outranks_pivots(self):
        page = render(
            pivots={T: pivots(2)},
            rewrite_status={T: {"state": "unavailable", "reason": "stale exact confirmation"}},
        )
        self.assertEqual("Rewrite analysis needs refresh", page.heading("diff"))
        self.assertEqual([], page.redline_links())
        self.assertNotRegex(page.summary + page.text("overview"), r"(?i)candidate rewrite window")

    @scenario("rewrite_status finding without any pivots does not read as a finding")
    def test_finding_without_pivots(self):
        try:
            page = render(rewrite_status={T: {"state": "finding"}})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"the topic page could not be generated: {type(exc).__name__}: {exc}")
        self.assertEqual("Rewrite analysis is not available", page.heading("diff"))
        self.assertEqual([], page.redline_links())
        self.assertNotRegex(page.text("diff") + page.summary, r"(?i)candidate rewrite window|confirmed")


# ---------------------------------------------------------------------------
# Rule 2: withheld or unavailable never reads as a finding or as no finding
# ---------------------------------------------------------------------------
class WithheldNeverReadsAsFindingOrNoFinding(unittest.TestCase):
    @scenario("A withheld confirmation hides its episodes and states no verdict")
    def test_withheld_hides_episodes(self):
        def check(row):
            page = render(confirmations={T: withheld(row["reason"], 2)})
            diff = page.text("diff")
            self.assertNotRegex(diff, r"(?i)\d+ confirmed rewrite episode")
            self.assertNotRegex(diff, r"(?i)no candidate rewrite window was (found|confirmed)")
            self.assertNotRegex(diff, r"(?i)\bhealthy\b")
            self.assertNotIn("View redline", page.html)
            exact = re.search(r"Exact check.*?<strong>(.*?)</strong>", page.panel("diff"), re.S)
            self.assertIsNotNone(exact)
            self.assertEqual("Unavailable", text_of(exact.group(1)))
        each_example("A withheld confirmation hides its episodes and states no verdict", check)

    @scenario("Withheld measured intervals are labelled as withheld")
    def test_withheld_intervals(self):
        def check(row):
            loss = float(row["loss"])
            page = render(confirmations={T: {
                "article": T, "status": "unavailable", "coarse_verdict": "PIVOT?",
                "reason": "artifact withheld: stable endpoint receipt is missing",
                "interval_profile": [interval(0, loss)],
            }})
            states = re.findall(r'<span class="drift-state">(.*?)</span>', page.html)
            self.assertEqual(1, len(states))
            self.assertNotIn("no anomaly threshold crossed", states[0])
            self.assertIn("exact verdict withheld", states[0])
            self.assertIn("neither confirm nor rule out a rewrite", page.text("diff"))
            self.assertIn("not a finding of no rewrite", page.text("diff"))
        each_example("Withheld measured intervals are labelled as withheld", check)

    @scenario("Each recorded unavailable reason gets its own plain explanation")
    def test_each_reason_has_its_own_copy(self):
        def check(row):
            page = render(rewrite_status={T: {"state": "unavailable", "reason": row["reason"]}})
            self.assertEqual(row["heading"], page.heading("diff"))
            self.assertEqual(row["heading"], page.rewrite_signal)
            diff = page.text("diff")
            self.assertNotRegex(diff, r"(?i)completed negative result")
            self.assertNotRegex(diff, r"(?i)no candidate rewrite window was")
        each_example("Each recorded unavailable reason gets its own plain explanation", check)

    @scenario("Reasons written by the redline export are not flattened into generic copy")
    def test_export_reasons_not_flattened(self):
        keywords = {"materialized": "materializ", "legacy": "legacy"}

        def check(row):
            page = render(rewrite_status={T: {"state": "unavailable", "reason": row["reason"]}})
            self.assertNotEqual("Rewrite analysis is not available", page.heading("diff"))
            key = next(word for word in keywords if word in row["reason"])
            self.assertIn(keywords[key], page.text("diff").lower())
        each_example("Reasons written by the redline export are not flattened into generic copy", check)

    @scenario("A withheld topic is listed as not available on the Overview")
    def test_withheld_listed_as_unavailable(self):
        page = render(confirmations={T: withheld("artifact withheld: stable endpoint receipt is missing")})
        have, missing = page.coverage()
        self.assertIn("Rewrite", missing)
        self.assertNotIn("Rewrite", have)
        self.assertIn("Missing coverage is not a negative finding", page.text("overview"))
        self.assertNotRegex(page.summary, r"(?i)confirmed rewrite")

    @scenario("A topic with only withheld evidence is not summarised as mild change")
    def test_withheld_only_not_mild_change(self):
        page = render(confirmations={T: withheld("artifact withheld: stable endpoint receipt is missing")})
        self.assertNotRegex(page.summary, r"(?i)mild change")
        self.assertRegex(page.summary, r"(?i)could not be completed|withheld|unavailable|not available")


# ---------------------------------------------------------------------------
# Rule 3: confirmed episodes always show redline links and pivot pages
# ---------------------------------------------------------------------------
class ConfirmedEpisodesHaveRedlines(unittest.TestCase):
    @scenario("Each confirmed episode links to a redline page that exists")
    def test_links_resolve_to_written_pages(self):
        with built_site(confirmations={T: confirmation(2)}, pivots={T: pivots(2)}) as site:
            page = Page((site / "article" / f"{T}.html").read_text(encoding="utf-8"))
            links = page.redline_links()
            self.assertEqual(2, len(links))
            self.assertEqual(2, len(set(links)))
            for link in links:
                self.assertTrue((site / "article" / link).is_file(), link)

    @scenario("Redlines are matched by candidate window or by exact revision pair")
    def test_redline_matching_keys(self):
        def check(row):
            if "window dates" in row["key"]:
                pv = pivots(1, before_rev=None, after_rev=None)
            else:
                pv = {"article": T, "pivots": [pivot(0, start="2009-01-01", end="2009-06-01")]}
            page = render(confirmations={T: confirmation(1)}, pivots={T: pv})
            self.assertIn("View redline", page.panel("diff"))
            self.assertNotIn("Redline unavailable", page.panel("diff"))
        each_example("Redlines are matched by candidate window or by exact revision pair", check)

    @scenario("A confirmed episode without an exported redline says so and never links to a dead page")
    def test_partial_redlines(self):
        with built_site(confirmations={T: confirmation(2)}, pivots={T: pivots(indexes=[0])}) as site:
            page = Page((site / "article" / f"{T}.html").read_text(encoding="utf-8"))
            diff = page.panel("diff")
            self.assertEqual(1, diff.count(">View redline</a>"))
            self.assertEqual(1, diff.count("Redline unavailable"))
            for link in page.redline_links():
                self.assertTrue((site / "article" / link).is_file(), link)

    @scenario("A confirmed topic is not published as a finding without any redline")
    def test_confirmed_without_pivots(self):
        def check(row):
            status = {}
            if "unavailable" in row["status"]:
                status = {T: {"state": "unavailable", "reason": "stale exact confirmation"}}
            page = render(confirmations={T: confirmation(3)}, rewrite_status=status)
            claims_confirmed = bool(re.search(r"\d+ confirmed rewrite episode", page.heading("diff")))
            linked = page.panel("diff").count(">View redline</a>")
            if claims_confirmed:
                self.assertEqual(3, linked, "confirmed headline but no redline for each episode")
            self.assertFalse(claims_confirmed and "Redline unavailable" in page.html and linked == 0)
        each_example("A confirmed topic is not published as a finding without any redline", check)

    @scenario("A control topic with a finding and its redlines stays clean")
    def test_control_topic(self):
        with built_site(confirmations={T: confirmation(3)}, pivots={T: pivots(3)},
                        rewrite_status={T: {"state": "finding"}}) as site:
            page = Page((site / "article" / f"{T}.html").read_text(encoding="utf-8"))
            self.assertEqual("3 confirmed rewrite episodes", page.heading("diff"))
            self.assertEqual("3 confirmed", page.rewrite_signal)
            self.assertIn("3 confirmed rewrite episodes", text_of((site / "findings.html").read_text(encoding="utf-8")))
            self.assertEqual(3, len(list((site / "article").glob(f"{T}.p*.html"))))
            self.assertEqual(3, len(page.redline_links()))

    @scenario("Redline page counts equal pivot counts")
    def test_redline_page_counts(self):
        with built_site(pivots={T: pivots(4)}) as site:
            page = Page((site / "article" / f"{T}.html").read_text(encoding="utf-8"))
            self.assertEqual(4, len(list((site / "article").glob(f"{T}.p*.html"))))
            self.assertEqual(4, len(set(page.panel("diff") and re.findall(r'href="([^"]*\.p\d+\.html)"', page.panel("diff")))))
            overview = re.findall(r'href="([^"]*\.p\d+\.html)"', page.panel("overview"))
            self.assertEqual(4, len(overview))
            self.assertIn("4 candidate windows", page.rewrite_signal)

    @scenario("A redline page identifies its evidence state and links back")
    def test_redline_page_state(self):
        confirmed = text_of(build.pivot_page(T, pivot(0, "confirmed"), 0))
        rejected = text_of(build.pivot_page(T, pivot(1, "rejected"), 1))
        self.assertIn("Confirmed candidate redline", confirmed)
        self.assertRegex(confirmed, r"(?i)coarse candidate window, not the exact event pair")
        self.assertIn("Rejected candidate redline", rejected)
        for markup in (build.pivot_page(T, pivot(0, "confirmed"), 0), build.pivot_page(T, pivot(1, "rejected"), 1)):
            self.assertIn(f'href="{T}.html', markup)

    @scenario("Coarse candidates without exact confirmation are never called confirmed")
    def test_coarse_candidates_not_confirmed(self):
        page = render(pivots={T: pivots(1, status=None)})
        self.assertEqual("Candidate rewrite window", next(
            label for label, _ in page.signals() if "rewrite" in label.lower()))
        shown = " ".join([page.summary, page.heading("diff"), " ".join(" ".join(s) for s in page.signals())])
        self.assertNotRegex(shown, r"(?i)confirmed")
        self.assertRegex(page.summary, r"(?i)candidate")


# ---------------------------------------------------------------------------
# Rule 4: interval metrics are intervals, not edits
# ---------------------------------------------------------------------------
def kokhba():
    spans = [("2025-01-01", "2025-07-01", 28.5, 87806), ("2025-07-01", "2026-01-01", 41.5, 106620),
             ("2026-01-01", "2026-07-01", 61.0, 117205)]
    return {
        "article": T, "status": "confirmed", "coarse_verdict": "PIVOT?", "thresholds": {"confirm_drop": 0.2},
        "interval_profile": [
            {"start": s, "end": e, "size": 41015, "pwr_loss": loss, "pwr_removed": removed,
             "pwr_gain": 8.8, "pwr_added": 16959, "confirmable": True, "anomaly_types": ["loss"]}
            for s, e, loss, removed in spans
        ],
        "evaluated_candidates": [{
            "candidate_start": "2025-01-01", "candidate_end": "2026-07-01", "decision": "confirmed",
            "durable_spine_drop": 0.8766, "peak_pct": 60.96, "pwr_mass": 311631,
            "exact_before_revid": 21, "exact_after_revid": 22, "exact_pair_drop": 0.6,
        }],
        "confirmed_episodes": [{
            "candidate_start": "2025-01-01", "candidate_end": "2026-07-01", "before_revid": 21,
            "after_revid": 22, "before_timestamp": "2026-03-01T00:00:00Z",
            "after_timestamp": "2026-03-01T00:44:00Z", "durable_spine_drop": 0.8766,
            "pwr_mass": 311631, "duration_seconds": 2657,
        }],
    }


class IntervalsAreNotEdits(unittest.TestCase):
    @scenario("Every interval row is labelled with its span and the chart says it is not an edit")
    def test_rows_labelled_with_span(self):
        page = render(confirmations={T: {
            "article": T, "status": "not_confirmed", "coarse_verdict": "HEALTHY",
            "interval_profile": [
                interval(0, 3.0, start="2026-01-01", end="2026-07-01"),
                interval(1, 4.0, start="2025-01-01", end="2026-01-01"),
            ],
        }})
        diff = page.text("diff")
        self.assertIn("181-day interval", diff)
        self.assertIn("365-day interval", diff)
        self.assertIn("loss across one snapshot interval, not a single edit", diff)
        self.assertIn("differs from the durable-spine drop", diff)

    @scenario("A 61% interval loss is never presented as a durable-spine drop or an edit")
    def test_61_percent_is_not_a_drop(self):
        page = render(confirmations={T: kokhba()})
        diff = page.text("diff")
        values = [text_of(v) for v in re.findall(r'<span class="drift-value">(.*?)</span>', page.html)]
        self.assertIn("Loss 61.0%, Gain 8.8%", values)
        self.assertTrue(all("durable" not in v for v in values), values)
        for match in re.finditer(r"87\.7%", page.text()):
            self.assertTrue(page.text()[match.end():].startswith(" durable-spine drop"), page.text()[match.start():match.end() + 30])
        self.assertNotRegex(diff, r"6[01](\.\d)?%[^.]{0,25}(durable-spine|of the article|rewritten|single edit)")
        self.assertIn("61% persistence-weighted loss", diff)

    @scenario("Chart values equal the interval data")
    def test_chart_values(self):
        page = render(confirmations={T: healthy_confirmation(interval_profile=[
            interval(0, 42.5, start="2026-01-01", end="2026-07-01", pwr_removed=12345, pwr_added=6789,
                     pwr_gain=8.8, anomaly_types=[]),
        ])})
        self.assertIn('style="width:42.50%"', page.html)
        self.assertIn("Loss 42.5%", page.text("diff"))
        self.assertIn("12,345 removed · 6,789 added PWR", page.text("diff"))

    @scenario("Missing gain fields are not shown as zero")
    def test_missing_gain_not_zero(self):
        def check(row):
            kwargs = {
                "no": {"pwr_gain": None, "pwr_added": None},
                "zero-valued": {"pwr_gain": 0.0, "pwr_added": 0},
                "positive": {"pwr_gain": 8.8, "pwr_added": 16959},
            }[row["recorded"]]
            interval_row = interval(0, 10.0, **kwargs)
            if kwargs["pwr_gain"] is None:
                interval_row.pop("pwr_gain"), interval_row.pop("pwr_added")
            page = render(confirmations={T: healthy_confirmation(interval_profile=[interval_row])})
            self.assertIn(row["added"], page.text("diff"))
            if row["recorded"] == "no":
                self.assertNotIn("0 added PWR", page.text("diff"))
        each_example("Missing gain fields are not shown as zero", check)

    @scenario("Elevated loss never reads as no anomaly")
    def test_elevated_loss_not_no_anomaly(self):
        def check(row):
            loss = float(row["loss"])
            base = {"confirmed": confirmation(1), "not_confirmed": rejected_confirmation(),
                    "unavailable": withheld(n=1)}[row["status"]]
            base = {**base, "interval_profile": [interval(0, loss, anomaly_types=[])]}
            page = render(confirmations={T: base})
            self.assertNotIn("no anomaly threshold crossed", page.html)
        each_example("Elevated loss never reads as no anomaly", check)

    @scenario("The detector path interval count equals the rows drawn")
    def test_interval_count(self):
        profile = [interval(i, 3.0, pwr_gain=1.0) for i in range(5)]
        page = render(confirmations={T: healthy_confirmation(interval_profile=profile)})
        self.assertIn("5 intervals scored", page.text("diff"))
        self.assertEqual(5, len(page.rows()))

    @scenario("Intervals inside a confirmed window carry the exact verdict and others do not")
    def test_covered_intervals(self):
        profile = [
            interval(0, 30.0, start="2010-01-01", end="2010-07-01"),
            interval(0, 31.0, start="2010-07-01", end="2011-01-01"),
            interval(1, 3.0, start="2012-01-01", end="2012-07-01", pwr_gain=1.0, anomaly_types=[]),
        ]
        page = render(confirmations={T: confirmation(1, interval_profile=profile)})
        states = [text_of(re.search(r'<span class="drift-state">(.*?)</span>', row).group(1)) for row in page.rows()]
        self.assertEqual(3, len(states))
        self.assertEqual(["Confirmed candidate window: 2010-01-01 → 2011-01-01"] * 2, states[:2])
        self.assertNotIn("candidate window", states[2].lower())

    @scenario("A micro exact pair is flagged instead of presented as the window localisation")
    def test_micro_pair(self):
        page = render(confirmations={T: confirmation(
            1,
            evaluated_candidates=[candidate(0, candidate_start="2025-01-01", candidate_end="2026-07-01",
                                            durable_spine_drop=0.88, exact_pair_drop=0.0044)],
            confirmed_episodes=[episode(0, candidate_start="2025-01-01", candidate_end="2026-07-01",
                                        durable_spine_drop=0.88)],
        )})
        diff = page.text("diff")
        self.assertIn("not a localisation", diff)
        self.assertNotIn("Exact pair:", diff)

    @scenario("A missing interval profile keeps the chart frame and invents nothing")
    def test_missing_profile(self):
        confirmation_data = {"article": T, "status": "not_confirmed", "coarse_verdict": "PIVOT?",
                             "evaluated_candidates": [candidate(0, "rejected")]}
        page = render(confirmations={T: confirmation_data})
        self.assertIn('class="drift-axis"', page.html)
        self.assertIn("Data missing", page.html)
        self.assertEqual(1, page.html.count("drift-row-missing"))
        self.assertNotIn('class="drift-bar"', page.html)


# ---------------------------------------------------------------------------
# Rule 5: the Framing tab agrees with the framing data and the rewrite verdict
# ---------------------------------------------------------------------------
def languages_compared(page):
    return [
        [lang.strip() for lang in group.split(",")]
        for group in re.findall(r"Languages compared: ([a-z, ]+)\.", page.text("framing"))
    ]


class FramingAgreesWithData(unittest.TestCase):
    @scenario("Languages compared match the framing data for every event")
    def test_languages_compared(self):
        def check(row):
            expected = [[lang.strip() for lang in group.split(",")]
                        for group in re.sub(r"\(.*?\)", "", row["editions"]).split("; and ")]
            if row["events"] == "a single comparison":
                page = render(framings={T: framing(expected[0], mode="static", win=None)}, receipts={T: RECEIPTS})
                self.assertEqual(expected, languages_compared(page))
                versions = re.findall(r"<td><b>(\w+)</b></td>", page.panel("revisions"))
                self.assertEqual(expected[0], versions)
                return
            events = [framing_event(i, langs) for i, langs in enumerate(expected)]
            if "out of order" in row["events"]:
                events.reverse()
            page = render(framings={T: {"article": T, "episodes": events}})
            self.assertEqual(expected, languages_compared(page))
        each_example("Languages compared match the framing data for every event", check)

    @scenario("Version receipts equal the framing snapshots")
    def test_version_receipts(self):
        fr = framing(("en", "fr"), snapshots=True)
        page = render(framings={T: fr}, receipts={T: RECEIPTS})
        rows = re.findall(
            r"<tr><td><b>(\w+)</b></td><td>(\w+)</td>.*?oldid=(\d+)", page.panel("revisions"), re.S)
        expected = [(lang, phase, str(fr["snapshots"][phase][lang]["revid"]))
                    for lang in ("en", "fr") for phase in ("before", "after")]
        self.assertEqual(expected, rows)

    @scenario("Candidate-relative framing is labelled as a candidate window, not a confirmed rewrite")
    def test_candidate_relative(self):
        page = render(
            framings={T: framing(mode="candidate_relative", win_status="candidate")},
            pivots={T: pivots(1, status=None)},
        )
        framing_text = page.text("framing")
        self.assertIn("endpoints of the L1 candidate window from 2010-01-01 to 2011-01-01", framing_text)
        self.assertNotIn("confirmed rewrite", framing_text)

    @scenario("Pivot-relative framing may say confirmed rewrite when the exact result supports it")
    def test_pivot_relative_supported(self):
        page = render(
            framings={T: framing()}, confirmations={T: confirmation(1)}, pivots={T: pivots(1)},
        )
        self.assertIn(
            "endpoints of the confirmed rewrite from 2010-01-01 to 2011-01-01", page.text("framing"))

    @scenario("Framing does not sell a confirmed rewrite while the rewrite verdict is withheld")
    def test_framing_while_withheld(self):
        def check(row):
            page = render(framings={T: framing()}, confirmations={T: withheld(row["reason"])})
            framing_text = page.text("framing")
            self.assertNotIn("confirmed rewrite", framing_text)
            self.assertRegex(framing_text, r"(?i)withheld|needs refresh|not tied to")
        each_example("Framing does not sell a confirmed rewrite while the rewrite verdict is withheld", check)

    @scenario("Pivot-relative framing needs a confirmed rewrite")
    def test_pivot_relative_needs_confirmation(self):
        def check(row):
            exact = rejected_confirmation() if "rejected" in row["result"] else healthy_confirmation()
            page = render(framings={T: framing()}, confirmations={T: exact})
            self.assertNotIn("confirmed rewrite", page.text("framing"))
        each_example("Pivot-relative framing needs a confirmed rewrite", check)

    @scenario("Pivot-relative framing for a different window than any confirmed episode is not confirmed")
    def test_pivot_relative_wrong_window(self):
        page = render(
            framings={T: framing(win=("2015-01-01", "2016-01-01"))},
            confirmations={T: confirmation(1)}, pivots={T: pivots(1)},
        )
        self.assertNotRegex(
            page.text("framing"), r"confirmed rewrite from 2015-01-01 to 2016-01-01")

    @scenario("Static framing does not claim an endpoint comparison")
    def test_static_not_endpoint(self):
        def check(row):
            mode = None if row["mode"].startswith("(") else row["mode"]
            page = render(framings={T: framing(mode=mode)})
            text = page.text("framing")
            self.assertNotIn("historical lead revisions at the endpoints", text)
        each_example("Static framing does not claim an endpoint comparison", check)

    @scenario("Failed or insufficient framing is absent and not a no-difference result")
    def test_failed_framing_absent(self):
        def build_row(state):
            if "provider error" in state:
                return {"article": T, "error": "provider request failed", "divergences": []}
            if "no model call" in state:
                return framing(calls=0, rows=0)
            return {"article": T, "episodes": [{
                "analysis_status": "unavailable", "reason": "no usable leads",
                "episode_window": {"start": "2010-01-01", "end": "2011-01-01"},
            }]}

        def check(row):
            page = render(framings={T: build_row(row["state"])})
            self.assertNotIn("Framing", page.tabs)
            self.assertIn("Framing", page.coverage()[1])
            self.assertNotIn("No clear differences", page.text())
        each_example("Failed or insufficient framing is absent and not a no-difference result", check)

    @scenario("A completed comparison with no divergences is a valid no-difference result")
    def test_no_divergences_is_valid(self):
        page = render(framings={T: framing(rows=0, calls=1)})
        self.assertIn("No clear differences were recorded", page.text("framing"))
        self.assertIsNone(page.signal("openings"))

    @scenario("Overview framing counts equal the Framing tab rows")
    def test_framing_counts(self):
        def check(row):
            rows, contradict = int(row["rows"]), int(row["contradict"])
            page = render(framings={T: framing(rows=rows, contradict=contradict)})
            body = re.search(r"<tbody>(.*?)</tbody>", page.panel("framing"), re.S).group(1)
            self.assertEqual(rows, body.count("<tr>"))
            self.assertEqual(row["signal"], page.signal("openings"))
        each_example("Overview framing counts equal the Framing tab rows", check)

    @scenario("The Overview only says framing compared around the rewrite window for temporal modes")
    def test_overview_framing_note(self):
        def check(row):
            page = render(framings={T: framing(mode=row["mode"])})
            note = page.text("overview")
            mentions = "Compared around the rewrite window" in note
            self.assertEqual(row["expectation"].startswith("says"), mentions)
        each_example("The Overview only says framing compared around the rewrite window for temporal modes", check)


# ---------------------------------------------------------------------------
# Rule 6: the Overview text matches the tabs
# ---------------------------------------------------------------------------
EVIDENCE = {
    "only an exact confirmation": lambda: dict(confirmations={T: confirmation(1)}),
    "a confirmation and lexical": lambda: dict(
        confirmations={T: confirmation(1)},
        lexical={T: {"article": T, "js_divergence": 0.3, "span": "x",
                     "before": {"date": "2010-01-01", "tokens": 100},
                     "after": {"date": "2012-01-01", "tokens": 110}}}),
    "lexical, citations, facts and framing": lambda: dict(
        lexical={T: {"article": T, "js_divergence": 0.3, "span": "x",
                     "before": {"date": "2010-01-01", "tokens": 100},
                     "after": {"date": "2012-01-01", "tokens": 110}}},
        sources={T: SOURCES}, factchecks={T: adjudication(["agree", "contradict"])},
        framings={T: framing(snapshots=True)}, receipts={T: RECEIPTS}),
    "a withheld confirmation and framing": lambda: dict(
        confirmations={T: withheld()}, framings={T: framing()}),
}
FAMILIES = ["Rewrite", "Vocabulary", "Citations", "Framing", "Facts", "Versions"]


class OverviewMatchesTabs(unittest.TestCase):
    @scenario("Available and unavailable evidence lists match the tabs present")
    def test_evidence_lists(self):
        def check(row):
            page = render(**EVIDENCE[row["evidence"]]())
            self.assertEqual([t.strip() for t in row["tabs"].split(",")], page.tabs)
            have, missing = page.coverage()
            normalise = lambda names: sorted(re.sub(r" \(.*\)", "", n) for n in names)  # noqa: E731
            rewrite_available = "confirmation" in row["evidence"] and "withheld" not in row["evidence"]
            expected_have = [t for t in page.tabs if t not in ("Overview", "Rewrite")]
            if rewrite_available:
                expected_have.append("Rewrite")
            self.assertEqual(sorted(expected_have), normalise(have))
            self.assertEqual(sorted(set(FAMILIES) - set(expected_have)), normalise(missing))
        each_example("Available and unavailable evidence lists match the tabs present", check)

    @scenario("Every in-page link from the Overview targets an existing tab")
    def test_overview_anchors(self):
        page = render(**full_findings())
        anchors = re.findall(r'href="#([^"]+)"', page.panel("overview"))
        self.assertTrue(anchors)
        for anchor in anchors:
            self.assertIn(anchor, page.panels, f"#{anchor} has no tab panel")

    @scenario("The Overview wording signal agrees with a Vocabulary tab built from several events")
    def test_lexical_events(self):
        second = {**LEXICAL_EVENT, "episode_window": {**LEXICAL_EVENT["episode_window"],
                                                      "before_revid": 3, "after_revid": 4}}
        page = render(lexical={T: {"article": T, "episodes": [LEXICAL_EVENT, second]}})
        self.assertIn("wording changed a lot", page.text("lexical"))
        self.assertEqual("wording changed a lot", page.signal("wording"))
        self.assertNotRegex(page.summary, r"(?i)only mild change")

    @scenario("Exact rejection is not listed as no candidate found")
    def test_rejection_not_no_candidate(self):
        page = render(confirmations={T: rejected_confirmation()})
        self.assertNotIn("no candidate found", page.text("overview"))
        self.assertIn("exact checking did not confirm a durable rewrite", page.text("overview"))

    @scenario("Footnote signal counts equal the Citations tab")
    def test_footnote_counts(self):
        page = render(sources={T: SOURCES})
        self.assertEqual("3 sites grew · 1 shrank", page.signal("footnotes"))
        sources = page.panel("sources")
        tables = re.findall(r"<tbody>(.*?)</tbody>", sources, re.S)
        self.assertEqual(
            [3, 1], [len(re.findall(r"<tr><td>[^<]+</td><td class=\"fromto\">", t)) for t in tables])
        self.assertIn("3 websites were cited more", text_of(sources))

    @scenario("Fact signal counts equal the Facts tab verdicts")
    def test_fact_counts(self):
        verdicts = ["contradict", "differ", "differ", "agree", "agree", "agree", "insufficient"]
        page = render(factchecks={T: adjudication(verdicts)})
        self.assertEqual("1 contradiction · 2 compatible differences · 3 agree · 1 not enough", page.signal("facts"))
        facts = page.panel("facts")
        counts = re.search(r"(\d+) contradict · (\d+) compatible difference · (\d+) agree · (\d+) not enough", text_of(facts))
        self.assertEqual(("1", "2", "3", "1"), counts.groups())
        labels = {"contradict": "contradict", "differ": "differ", "agree": "agree", "insufficient": "not enough said"}
        for i, verdict in enumerate(verdicts):
            cell = re.search(rf'<th scope="row">Question {i}\?</th><td[^>]*>(.*?)</td>', facts, re.S)
            self.assertEqual(labels[verdict], text_of(cell.group(1)))
        self.assertIn("1 factual question out of 7 contradicts across languages", page.summary)

    @scenario("Candidate windows on the Overview equal the Rewrite tab")
    def test_candidate_windows(self):
        page = render(pivots={T: pivots(2)})
        for slug in ("overview", "diff"):
            text = page.text(slug)
            for i in range(2):
                self.assertIn(" → ".join(window(i)), text, slug)
            self.assertEqual(2, len(set(re.findall(r'href="([^"]*\.p\d+\.html)"', page.panel(slug)))), slug)

    @scenario("The topic page summary is the same sentence as its findings card")
    def test_summary_equals_card(self):
        with built_site(confirmations={T: confirmation(1)}, pivots={T: pivots(1)},
                        framings={T: framing(rows=2)}) as site:
            page = Page((site / "article" / f"{T}.html").read_text(encoding="utf-8"))
            listing = (site / "findings.html").read_text(encoding="utf-8")
            self.assertTrue(page.summary)
            self.assertIn(page.summary, text_of(listing))


# ---------------------------------------------------------------------------
# Rule 7: counts and percentages match the data
# ---------------------------------------------------------------------------
class NumbersMatchData(unittest.TestCase):
    @scenario("Numbers on the page are formatted from the recorded values")
    def test_formatting(self):
        page = render(confirmations={T: kokhba()})
        text = page.text()
        for row in examples("Numbers on the page are formatted from the recorded values"):
            self.assertIn(row["shown"], text, row)

    @scenario("The strongest episode is chosen by persistence-weighted mass")
    def test_strongest_by_mass(self):
        eps = [episode(0, durable_spine_drop=0.3, pwr_mass=900), episode(1, durable_spine_drop=0.8, pwr_mass=100),
               episode(2, durable_spine_drop=0.55, pwr_mass=5000)]
        page = render(confirmations={T: confirmation(3, confirmed_episodes=eps)})
        note = page.text("overview")
        self.assertIn("55.0% durable-spine drop", note)
        self.assertNotIn("80.0% durable-spine drop", note)


# ---------------------------------------------------------------------------
# Rule 8: no dead links
# ---------------------------------------------------------------------------
class NoDeadLinks(unittest.TestCase):
    @scenario("Every internal link and anchor on the built topic and redline pages resolves")
    def test_internal_links_resolve(self):
        with built_site(**full_findings()) as site:
            files = [site / "article" / f"{T}.html", *sorted((site / "article").glob(f"{T}.p*.html"))]
            self.assertEqual(3, len(files))
            self.assertEqual([], dead_links(site, files))

    @scenario("Revision links use the recorded revision ids and language hosts")
    def test_revision_links(self):
        data = full_findings()
        recorded = {("en", str(e["before_revid"])) for e in data["confirmations"][T]["confirmed_episodes"]}
        recorded |= {("en", str(e["after_revid"])) for e in data["confirmations"][T]["confirmed_episodes"]}
        recorded |= {("en", str(c["exact_before_revid"])) for c in data["confirmations"][T]["evaluated_candidates"]}
        recorded |= {("en", str(c["exact_after_revid"])) for c in data["confirmations"][T]["evaluated_candidates"]}
        for phase in ("before", "after"):
            recorded |= {(lang, str(v["revid"])) for lang, v in data["framings"][T]["snapshots"][phase].items()}
        recorded |= {(lang, str(v["revid"])) for lang, v in RECEIPTS["editions"].items()}
        page = render(**data)
        links = re.findall(r"https://([a-z-]+)\.wikipedia\.org/w/index\.php\?oldid=(\d+)", page.html)
        self.assertTrue(links)
        self.assertEqual([], [link for link in links if link not in recorded])

    @scenario("The topic is reachable from the findings list")
    def test_reachable(self):
        with built_site(confirmations={T: confirmation(1)}, pivots={T: pivots(1)}) as site:
            listing = (site / "findings.html").read_text(encoding="utf-8")
            self.assertIn(f'article/{T}.html', listing)
            self.assertTrue((site / "article" / f"{T}.html").is_file())
            self.assertIn('href="../findings.html"', (site / "article" / f"{T}.html").read_text(encoding="utf-8"))

    @scenario("Withheld topics do not leave redline links behind")
    def test_withheld_no_redline_links(self):
        with built_site(confirmations={T: withheld()}, pivots={T: pivots(2)}) as site:
            page = Page((site / "article" / f"{T}.html").read_text(encoding="utf-8"))
            self.assertEqual([], page.redline_links())
            self.assertEqual([], dead_links(site, [site / "article" / f"{T}.html"]))

    @scenario("Withheld topics do not publish orphan redline pages")
    def test_withheld_no_orphan_pages(self):
        with built_site(confirmations={T: withheld()}, pivots={T: pivots(2)}) as site:
            self.assertEqual([], sorted(p.name for p in (site / "article").glob(f"{T}.p*.html")))


if __name__ == "__main__":
    unittest.main()

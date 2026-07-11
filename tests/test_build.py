"""Characterization tests for the viewer's HTML rendering (viewer/build.py).

These pin the observable rendered output for a fixed synthetic input so the `gather()`/`article_page()`
parameter-object refactor can be proven behavior-preserving. build.py is a stdlib-only module with no
import-time side effects (main() is guarded), so it imports cleanly once viewer/ is on the path. The tests
run against BOTH the pre-refactor positional signature and the post-refactor `Findings` object (dual-mode),
so the same assertions guard the output across the change.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "viewer"))
import build  # noqa: E402


REC = {"article": "Testland", "qid": "Q42",
       "editions": {"en": {"present": True, "revid": 123, "title": "Testland",
                           "timestamp": "2020-01-01", "prose_chars": 100}}}
ST = {"article": "Testland", "langs": ["en", "he"], "entities": ["Israel"],
      "editions": {"en": {"lead": {"Israel": {"stance": "neutral", "npov_departure": False, "evidence": "x"}}},
                   "he": {"lead": {"Israel": {"stance": "critical", "npov_departure": True, "evidence": "y"}}}}}
DIVER = {"static": {"Testland": {"variants": {"lead": {"divergence": 1.0}, "focal": {"divergence": 1.0}}}},
         "pivot_relative": {}}


def _article_html():
    if hasattr(build, "Findings"):
        f = build.Findings(receipts={"Testland": REC}, stances={"Testland": ST}, diver=DIVER)
        return build.article_page("Testland", f)
    return build.article_page("Testland", REC, ST, DIVER, {}, {}, None, None, None, None, None)


def _index_html():
    if hasattr(build, "Findings"):
        return build.index_page(["Testland"], build.Findings(stances={"Testland": ST}, diver=DIVER))
    return build.index_page(["Testland"], {"stances": {"Testland": ST}, "diver": DIVER,
                                           "factchecks": {}, "mscore": {}})


class ArticlePageRendering(unittest.TestCase):
    def test_renders_title_category_and_receipts(self):
        out = _article_html()
        self.assertIn("Testland", out)
        self.assertIn("rev 123", out)          # receipts revision link
        self.assertIn("Q42", out)              # Wikidata QID
        self.assertIn("Receipts", out)         # receipts tab present

    def test_renders_framing_stance_grid(self):
        out = _article_html()
        self.assertIn("Framing", out)          # framing tab
        self.assertIn("critical", out)         # he edition stance rendered
        self.assertIn("Israel", out)           # focal entity row

    def test_index_lists_the_article_with_its_link(self):
        idx = _index_html()
        self.assertIn("Testland", idx)
        self.assertIn('href="article/Testland.html"', idx)


if __name__ == "__main__":
    unittest.main()

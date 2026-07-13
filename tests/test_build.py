"""Characterization tests for the viewer's HTML rendering (viewer/build.py)."""
import pathlib
import sys
import unittest

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


class ArticlePageRendering(unittest.TestCase):
    def test_renders_title_and_versions(self):
        out = _article_html()
        self.assertIn("Testland", out)
        self.assertIn("123", out)
        self.assertIn("Q42", out)
        self.assertIn("Versions", out)

    def test_renders_framing_stance_grid(self):
        out = _article_html()
        self.assertIn("Framing", out)
        self.assertIn("more critical", out)
        self.assertIn("Israel", out)
        self.assertIn("Start here", out)

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


class SiteRouting(unittest.TestCase):
    def test_homepage_is_about(self):
        about = build.simple_page("About", "<h1>About WikiDrift</h1>", "about", path="index.html")
        self.assertIn("About WikiDrift", about)
        self.assertIn('href="findings.html"', about)
        self.assertIn("How it works", about)
        self.assertIn("Reading tips", about)


if __name__ == "__main__":
    unittest.main()

import json
import pathlib
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()

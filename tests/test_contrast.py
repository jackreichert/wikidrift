"""The viewer's OKLCH palette must stay WCAG 2.1 AA — regression guard on every design change."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "viewer"))  # check_contrast isn't installed
import check_contrast as cc  # noqa: E402


class ContrastAA(unittest.TestCase):
    def test_every_rendered_pair_passes_AA(self):
        failures = [(fg, bg, cc.contrast(fg, bg), cc.AA[kind])
                    for fg, bg, kind, _ in cc.PAIRS if cc.contrast(fg, bg) < cc.AA[kind]]
        self.assertEqual(failures, [], f"AA failures: {failures}")

    def test_body_text_is_high_contrast(self):
        # editorial monochrome: near-black ink on white should be well above the 4.5 floor
        self.assertGreater(cc.contrast("ink", "paper"), 12.0)


if __name__ == "__main__":
    unittest.main()

"""Characterization tests for the shared config helpers — written BEFORE splitting config.py into a
package, so the split (facade re-exports) is proven behavior-preserving. These are the pure/logic bits;
paths and provider tables are plain constants that the facade re-export covers structurally.
"""
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from wikidrift import config


class Slugify(unittest.TestCase):
    def test_spaces_and_path_separators_collapse(self):
        self.assertEqual(config.slugify("A B"), "A_B")
        self.assertEqual(config.slugify("A/B"), "A_B")     # CWE-22: no nested/escaping path
        self.assertEqual(config.slugify("A\\B"), "A_B")
        self.assertEqual(config.slugify(".."), "__")
        self.assertEqual(config.slugify("A\x00B"), "A_B")

    def test_preserves_unicode_letters(self):
        self.assertEqual(config.slugify("Israeli–Palestinian conflict"), "Israeli–Palestinian_conflict")

    def test_confirmation_thresholds_exposes_persisted_contract(self):
        self.assertEqual(config.confirmation_thresholds(), {
            "confirm_drop": config.CONFIRM_DROP,
            "durable_quantile": config.DURABLE_Q,
            "min_cohort": config.MIN_COHORT,
            "magnitude_floor": config.MAG_FLOOR,
            "rolling_window_months": config.ROLLING_WINDOW_MONTHS,
            "rolling_tolerance_days": config.ROLLING_TOLERANCE_DAYS,
            "rolling_drop": config.ROLLING_DROP,
        })


class StoragePaths(unittest.TestCase):
    def test_data_dir_environment_override_is_applied_in_a_fresh_process(self):
        custom = pathlib.Path("/tmp/wikidrift-test-shard")
        environment = os.environ.copy()
        environment["WIKIDRIFT_DATA_DIR"] = str(custom)

        output = subprocess.check_output(
            [sys.executable, "-c", "from wikidrift import config; print(config.DATA_DIR)"],
            env=environment,
            text=True,
        ).strip()

        self.assertEqual(pathlib.Path(output), custom)


class CitationDomains(unittest.TestCase):
    def test_extracts_and_strips_www(self):
        self.assertEqual(config.citation_domains("[https://www.nytimes.com/x foo]"), {"nytimes.com": 1})

    def test_wayback_unwrap_is_opt_in(self):
        raw = "[https://web.archive.org/web/2020/https://bbc.co.uk/y z]"
        self.assertEqual(config.citation_domains(raw, unwrap_wayback=True), {"bbc.co.uk": 1})
        self.assertIn("web.archive.org", config.citation_domains(raw))   # not unwrapped by default


class AnonIp(unittest.TestCase):
    def test_matches_ipv4_and_ipv6_not_usernames(self):
        self.assertTrue(config.ANON_IP_RE.match("1.2.3.4"))
        self.assertTrue(config.ANON_IP_RE.match("2001:db8::1"))
        self.assertIsNone(config.ANON_IP_RE.match("Alice"))


class GetJsonRetrying(unittest.TestCase):
    def test_returns_parsed_json_on_first_success(self):
        class _Resp:
            def json(self):
                return {"ok": True}

        class _Sess:
            def get(self, url, params=None, timeout=0):
                return _Resp()
        self.assertEqual(config.get_json_retrying(_Sess(), "u"), {"ok": True})

    def test_retries_then_reraises_after_attempts(self):
        calls = {"n": 0}

        class _Sess:
            def get(self, url, params=None, timeout=0):
                calls["n"] += 1
                raise ValueError("boom")
        with mock.patch("time.sleep"):                      # don't actually back off
            with self.assertRaises(ValueError):
                config.get_json_retrying(_Sess(), "u", attempts=3)
        self.assertEqual(calls["n"], 3)                     # tried exactly `attempts` times


if __name__ == "__main__":
    unittest.main()

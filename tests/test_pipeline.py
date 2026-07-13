"""The L1→L2→L5 orchestration must follow the pre-rank router (the 'adjudicate routed leads' gap).

Offline only (no LLM, no network): asserts the routing DECISION, not the LLM adjudication itself.
"""
import contextlib
import io
import unittest

from wikidrift import config, pipeline


@unittest.skipUnless(config.DB.exists(), "provenance.duckdb corpus not present")
class Orchestration(unittest.TestCase):
    def _run(self, article):
        with contextlib.redirect_stdout(io.StringIO()):    # pipeline prints a report; keep test output clean
            return pipeline.run(article, llm=False, corroborate=False)

    def test_nakba_routes_addition_to_L2(self):
        # canonical born-framed / addition case: L1 reads HEALTHY, the router catches reframe-by-addition
        r = self._run("Nakba")
        self.assertIn("HEALTHY", r["l1"])
        self.assertIn("addition→L2", r["leads"])
        self.assertFalse(r["l2_adjudicated"])              # offline ⇒ pending --llm, not adjudicated

    def test_result_shape(self):
        r = self._run("Photosynthesis")
        self.assertEqual(set(r), {"article", "l1", "leads", "l2_adjudicated", "mscore", "lexical", "l5"})


if __name__ == "__main__":
    unittest.main()

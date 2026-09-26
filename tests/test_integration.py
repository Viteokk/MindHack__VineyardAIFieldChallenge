"""Integration tests on the real data and on synthetic edge cases (pipeline/qa.py), one test per QA check.

They need the tiles and the pipeline outputs, and take ~2 minutes, so they run on request:
    VINEPLAN_INTEGRATION=1 python -m unittest tests.test_integration
A FAIL fails the test; a WARN (known limitation, explained in QA_REPORT.md) does not.
"""
import os
import unittest

from pipeline import qa

ENABLED = os.environ.get("VINEPLAN_INTEGRATION") == "1" and qa.data_ready()


@unittest.skipUnless(ENABLED, "set VINEPLAN_INTEGRATION=1 (needs data/tiles and the pipeline outputs)")
class QAChecks(unittest.TestCase):
    pass


def _make(cid, fn):
    def test(self):
        r = fn()
        self.assertNotEqual(r["status"], "FAIL", f"{cid}: {r['measured']} (threshold {r['threshold']})")
    return test


for cid, level, title, fn in qa.CHECKS:
    if level != "perf":
        setattr(QAChecks, f"test_{cid}_{level}", _make(cid, fn))

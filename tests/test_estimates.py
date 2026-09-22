"""
Run from the project folder:   py -m unittest discover -s tests -v
estimates.py: Alpha Vantage EARNINGS_ESTIMATES parsing/matching. All network calls mocked;
the real shape was confirmed live against AAPL 2026-09-22 (see project notes).
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import estimates as E                                          # noqa: E402


def row(d, eps, rev, horizon="fiscal quarter", analysts=30):
    return {"date": d, "horizon": horizon, "eps_estimate_average": str(eps),
           "revenue_estimate_average": str(rev), "eps_estimate_analyst_count": str(analysts)}


class FindEstimate(unittest.TestCase):
    def test_matches_the_nearest_quarter_within_tolerance(self):
        rows = [row("2026-06-30", 1.8924, 108959817930)]
        got = E.find_estimate(rows, date(2026, 6, 27))
        self.assertEqual(got["eps"], 1.8924)
        self.assertEqual(got["revenue"], 108959817930.0)
        self.assertEqual(got["analyst_count"], 30)

    def test_out_of_tolerance_date_finds_nothing(self):
        rows = [row("2026-06-30", 1.8924, 108959817930)]
        self.assertIsNone(E.find_estimate(rows, date(2026, 3, 1)))

    def test_wrong_horizon_is_ignored(self):
        rows = [row("2026-09-30", 8.8195, 477832817030, horizon="fiscal year")]
        self.assertIsNone(E.find_estimate(rows, date(2026, 9, 30), horizon="fiscal quarter"))

    def test_picks_the_closest_when_two_rows_are_in_range(self):
        # 2026-06-25 is 2 days from the target, 2026-06-30 is 3 days -- the nearer one wins
        rows = [row("2026-06-25", 1.80, 100), row("2026-06-30", 1.8924, 108959817930)]
        got = E.find_estimate(rows, date(2026, 6, 27), tolerance_days=5)
        self.assertEqual(got["eps"], 1.80)

    def test_a_missing_field_is_none_not_a_crash(self):
        rows = [{"date": "2026-06-30", "horizon": "fiscal quarter",
                "eps_estimate_average": None, "revenue_estimate_average": "108959817930"}]
        got = E.find_estimate(rows, date(2026, 6, 27))
        self.assertIsNone(got["eps"])
        self.assertEqual(got["revenue"], 108959817930.0)


class Surprise(unittest.TestCase):
    def test_beat_is_positive(self):
        s = E.surprise(2.02, 1.8924)
        self.assertAlmostEqual(s["abs"], 0.1276, places=4)
        self.assertAlmostEqual(s["pct"], 6.74, places=1)

    def test_miss_is_negative(self):
        s = E.surprise(1.80, 1.8924)
        self.assertLess(s["pct"], 0)

    def test_zero_estimate_does_not_divide_by_zero(self):
        s = E.surprise(1.0, 0)
        self.assertIsNone(s["pct"])
        self.assertEqual(s["abs"], 1.0)


class RealAAPLIncident(unittest.TestCase):
    """Pins the real 2026-09-22 finding: AAPL beat both estimates the June quarter, and its
    own September-quarter guidance sits right at the top of (roughly in line with) what
    analysts already expected -- not a clear raise or lower."""

    def test_aapl_june_quarter_beat_both(self):
        rows = [row("2026-06-30", 1.8924, 108959817930)]
        est = E.find_estimate(rows, date(2026, 6, 27))
        rev_s = E.surprise(109417000000, est["revenue"])
        eps_s = E.surprise(2.02, est["eps"])
        self.assertGreater(rev_s["pct"], 0)
        self.assertGreater(eps_s["pct"], 0)

    def test_guided_range_brackets_the_implied_consensus_growth(self):
        prior_year_actual = 102466000000
        guided_lo, guided_hi = prior_year_actual * 1.09, prior_year_actual * 1.11
        consensus_revenue = 113624521680.0
        self.assertTrue(guided_lo <= consensus_revenue <= guided_hi * 1.001)  # essentially at the top


class Caching(unittest.TestCase):
    def test_fetch_writes_and_reuses_the_cache(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(E, "_request", return_value={"estimates": [row("2026-06-30", 1.0, 1)]}) as req:
            a = E.fetch_estimates("AAPL", api_key="x", cache_dir=Path(d))
            b = E.fetch_estimates("AAPL", api_key="x", cache_dir=Path(d))
        self.assertEqual(a, b)
        self.assertEqual(req.call_count, 1)

    def test_refresh_forces_a_new_request(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(E, "_request", return_value={"estimates": []}) as req:
            E.fetch_estimates("AAPL", api_key="x", cache_dir=Path(d))
            E.fetch_estimates("AAPL", api_key="x", cache_dir=Path(d), refresh=True)
        self.assertEqual(req.call_count, 2)

    def test_no_api_key_raises_a_clear_error_not_a_network_call(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(E, "_request") as req:
            with self.assertRaises(E.AlphaVantageError):
                E.fetch_estimates("AAPL", cache_dir=Path(d))
        req.assert_not_called()


if __name__ == "__main__":
    unittest.main()

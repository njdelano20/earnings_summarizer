"""
Run from the project folder:   py -m unittest discover -s tests -v
sec_edgar.py: cross-checking the extractor's headline figures against SEC EDGAR's XBRL data. All network calls are
mocked here; live-network verification against real AMD data was done separately during development (see the
project notes) and found a real design gap (GAAP vs non-GAAP), which these tests pin down.
"""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import sec_edgar as S                                          # noqa: E402


def usd_entries(*rows):
    """rows: (start, end, val, form). Builds the {"units": {"USD": [...]}} shape SEC returns for one concept."""
    return {"units": {"USD": [{"start": s, "end": e, "val": v, "form": f, "fy": 2026, "fp": "Q2",
                              "filed": "2026-08-05"} for s, e, v, f in rows]}}


def facts_with(concept, *rows):
    return {"cik": 2488, "entityName": "TEST CO", "facts": {"us-gaap": {concept: usd_entries(*rows)}}}


class Ciks(unittest.TestCase):
    def test_real_cik_snapshot_has_amd(self):
        self.assertEqual(S.cik_for("AMD"), "0000002488")

    def test_unknown_symbol_and_none(self):
        self.assertIsNone(S.cik_for("ZZZZNOTREAL"))
        self.assertIsNone(S.cik_for(None))


class QuarterlyMatching(unittest.TestCase):
    def test_picks_the_standalone_quarter_not_the_ytd_figure(self):
        facts = facts_with("RevenueFromContractWithCustomerExcludingAssessedTax",
                           ("2025-12-28", "2026-06-27", 21789000000, "10-Q"),   # 6-month YTD: must be excluded
                           ("2026-03-29", "2026-06-27", 11536000000, "10-Q"))   # the real standalone quarter
        got = S.find_quarterly_value(facts, "revenue", date(2026, 6, 27))
        self.assertEqual(got["value"], 11536000000)

    def test_annual_10k_duration_is_excluded_for_a_quarter_lookup(self):
        facts = facts_with("Revenues", ("2025-12-29", "2026-12-27", 34639000000, "10-K"))
        self.assertIsNone(S.find_quarterly_value(facts, "revenue", date(2026, 6, 27)))

    def test_tolerates_a_few_days_of_fiscal_calendar_drift(self):
        # the caller often only has year-month (periods.py tracks no exact day); month-end is a few days off a
        # 52/53-week company's real quarter-end
        facts = facts_with("Revenues", ("2026-03-29", "2026-06-27", 11536000000, "10-Q"))
        got = S.find_quarterly_value(facts, "revenue", date(2026, 6, 30), tolerance_days=3)
        self.assertEqual(got["value"], 11536000000)

    def test_out_of_tolerance_date_finds_nothing(self):
        facts = facts_with("Revenues", ("2026-03-29", "2026-06-27", 11536000000, "10-Q"))
        self.assertIsNone(S.find_quarterly_value(facts, "revenue", date(2026, 3, 28), tolerance_days=3))

    def test_falls_back_through_concept_candidates_in_priority_order(self):
        facts = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {
            "Revenues": usd_entries(("2026-03-29", "2026-06-27", 999, "10-Q"))}}}
        got = S.find_quarterly_value(facts, "revenue", date(2026, 6, 27))
        self.assertEqual(got["value"], 999)          # RevenueFromContractWithCustomerExcludingAssessedTax absent

    def test_no_concept_present_at_all_returns_none(self):
        self.assertIsNone(S.find_quarterly_value({"facts": {"us-gaap": {}}}, "revenue", date(2026, 6, 27)))

    def test_unmapped_metric_returns_none(self):
        self.assertIsNone(S.find_quarterly_value({"facts": {"us-gaap": {}}}, "not_a_real_metric", date(2026, 6, 27)))

    def test_eps_uses_the_per_share_unit_not_usd(self):
        facts = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {"EarningsPerShareDiluted": {
            "units": {"USD/shares": [{"start": "2026-03-29", "end": "2026-06-27", "val": 1.38, "form": "10-Q"}]}}}}}
        got = S.find_quarterly_value(facts, "eps", date(2026, 6, 27))
        self.assertEqual(got["value"], 1.38)
        self.assertEqual(got["unit"], "USD_per_share")


class CrossCheck(unittest.TestCase):
    def _facts_result(self, *facts):
        return {"facts": [{"id": f"X-{i}", "kind": "reported", "metric": m, "segment": "total", "stat": "level",
                          "value": v, "accounting": acc}
                          for i, (m, v, acc) in enumerate(facts)]}

    def test_revenue_within_tolerance_passes(self):
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "RevenueFromContractWithCustomerExcludingAssessedTax", ("2026-03-29", "2026-06-27", 11536000000, "10-Q"))):
            checks = S.check_against_sec(self._facts_result(("revenue", 11.5e9, None)), "AMD", date(2026, 6, 27))
        self.assertEqual([c["status"] for c in checks], ["pass"])

    def test_a_real_gaap_confirmed_mismatch_warns(self):
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "OperatingIncomeLoss", ("2026-03-29", "2026-06-27", 1990000000, "10-Q"))):
            checks = S.check_against_sec(self._facts_result(("operating_income", 3.1e9, "gaap")), "AMD", date(2026, 6, 27))
        self.assertEqual(checks[0]["status"], "warn")

    def test_the_amd_incident_an_unlabelled_non_gaap_figure_does_not_warn(self):
        # the real 2026-09-22 case: AMD's call defaults to non-GAAP without saying so on every line; a raw
        # comparison against SEC's GAAP-only tag must not be reported as an extraction error
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "OperatingIncomeLoss", ("2026-03-29", "2026-06-27", 1990000000, "10-Q"))):
            checks = S.check_against_sec(self._facts_result(("operating_income", 3.1e9, None)), "AMD", date(2026, 6, 27))
        self.assertEqual(checks[0]["status"], "unlabeled_accounting_basis")
        self.assertNotIn("warn", [c["status"] for c in checks])

    def test_revenue_always_compared_even_when_unlabelled(self):
        # revenue is in _ALWAYS_COMPARABLE regardless of the accounting field
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "Revenues", ("2026-03-29", "2026-06-27", 6.7e9, "10-Q"))):
            checks = S.check_against_sec(self._facts_result(("revenue", 11.5e9, None)), "AMD", date(2026, 6, 27))
        self.assertEqual(checks[0]["status"], "warn")     # the real AMD $6.7B/$11.5B incident, reproduced

    def test_a_metric_never_extracted_is_not_checked(self):
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "Revenues", ("2026-03-29", "2026-06-27", 11.5e9, "10-Q"))):
            checks = S.check_against_sec(self._facts_result(), "AMD", date(2026, 6, 27))
        self.assertEqual(checks, [])

    def test_no_matching_sec_period_is_no_data_not_a_failure(self):
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "Revenues", ("2025-03-29", "2025-06-27", 1, "10-Q"))):        # a year off: no match
            checks = S.check_against_sec(self._facts_result(("revenue", 11.5e9, None)), "AMD", date(2026, 6, 27))
        self.assertEqual(checks[0]["status"], "no_data")

    def test_no_cik_is_no_data_not_a_crash(self):
        checks = S.check_against_sec(self._facts_result(("revenue", 1, None)), "ZZZZNOTREAL", date(2026, 6, 27))
        self.assertEqual(checks[0]["status"], "no_data")
        self.assertEqual(checks[0]["name"], "sec_xbrl")

    def test_a_fact_with_no_value_is_skipped(self):
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "Revenues", ("2026-03-29", "2026-06-27", 1, "10-Q"))):
            checks = S.check_against_sec(self._facts_result(("revenue", None, None)), "AMD", date(2026, 6, 27))
        self.assertEqual(checks, [])


def submissions_with(*rows):
    """rows: (form, reportDate). Builds the parallel-array shape SEC's submissions API returns."""
    return {"filings": {"recent": {"form": [r[0] for r in rows], "reportDate": [r[1] for r in rows]}}}


class PeriodRefinement(unittest.TestCase):
    def test_refines_to_the_real_reportdate_when_close_to_the_guess(self):
        # the caller's crude month-end guess (2026-07-02) is 5 days off the real filed period end -- too far for
        # find_quarterly_value's normal 3-day tolerance, but well within the 45-day submissions search window
        with mock.patch.object(S, "fetch_submissions", return_value=submissions_with(
                ("10-Q", "2026-06-27"), ("10-K", "2025-12-27"))):
            got, exact = S._refine_period_end("AMD", date(2026, 7, 2))
        self.assertEqual(got, date(2026, 6, 27))
        self.assertTrue(exact)

    def test_ignores_forms_that_are_not_10q_or_10k(self):
        with mock.patch.object(S, "fetch_submissions", return_value=submissions_with(("8-K", "2026-06-28"))):
            got, exact = S._refine_period_end("AMD", date(2026, 6, 30))
        self.assertEqual(got, date(2026, 6, 30))
        self.assertFalse(exact)

    def test_nothing_within_the_search_window_falls_back_to_the_guess(self):
        with mock.patch.object(S, "fetch_submissions", return_value=submissions_with(("10-Q", "2025-06-27"))):
            got, exact = S._refine_period_end("AMD", date(2026, 6, 30))
        self.assertEqual(got, date(2026, 6, 30))
        self.assertFalse(exact)

    def test_a_lookup_failure_falls_back_to_the_guess_not_a_crash(self):
        with mock.patch.object(S, "fetch_submissions", side_effect=S.SECEdgarError("no CIK")):
            got, exact = S._refine_period_end("ZZZZNOTREAL", date(2026, 6, 30))
        self.assertEqual(got, date(2026, 6, 30))
        self.assertFalse(exact)

    def test_picks_the_closest_reportdate_when_several_are_in_range(self):
        with mock.patch.object(S, "fetch_submissions", return_value=submissions_with(
                ("10-Q", "2026-06-20"), ("10-Q", "2026-06-27"))):
            got, exact = S._refine_period_end("AMD", date(2026, 6, 29))
        self.assertEqual(got, date(2026, 6, 27))
        self.assertTrue(exact)


class CrossCheckWithSubmissions(unittest.TestCase):
    def _facts_result(self, *facts):
        return {"facts": [{"id": f"X-{i}", "kind": "reported", "metric": m, "segment": "total", "stat": "level",
                          "value": v, "accounting": acc}
                          for i, (m, v, acc) in enumerate(facts)]}

    def test_end_to_end_refinement_produces_a_pass_the_crude_guess_would_have_missed(self):
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "Revenues", ("2026-03-29", "2026-06-27", 11536000000, "10-Q"))), \
             mock.patch.object(S, "fetch_submissions", return_value=submissions_with(("10-Q", "2026-06-27"))):
            checks = S.check_against_sec(self._facts_result(("revenue", 11.5e9, None)), "AMD", date(2026, 7, 2))
        self.assertEqual(checks[0]["status"], "pass")

    def test_no_submissions_data_still_falls_back_to_the_plain_tolerance(self):
        with mock.patch.object(S, "fetch_company_facts", return_value=facts_with(
                "Revenues", ("2026-03-29", "2026-06-27", 11536000000, "10-Q"))), \
             mock.patch.object(S, "fetch_submissions", side_effect=S.SECEdgarError("no CIK")):
            checks = S.check_against_sec(self._facts_result(("revenue", 11.5e9, None)), "AMD", date(2026, 6, 29))
        self.assertEqual(checks[0]["status"], "pass")   # 2 days off, within the plain 3-day tolerance


class Caching(unittest.TestCase):
    def test_fetch_writes_and_reuses_the_cache(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(S, "_request", return_value={"ok": True}) as req:
            a = S.fetch_company_facts("AMD", cache_dir=Path(d))
            b = S.fetch_company_facts("AMD", cache_dir=Path(d))
        self.assertEqual(a, {"ok": True})
        self.assertEqual(b, {"ok": True})
        self.assertEqual(req.call_count, 1)              # second call served from cache, no second request

    def test_submissions_cache_is_separate_from_company_facts_cache(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(S, "_request", return_value={"ok": True}) as req:
            S.fetch_company_facts("AMD", cache_dir=Path(d))
            S.fetch_submissions("AMD", cache_dir=Path(d))
            S.fetch_submissions("AMD", cache_dir=Path(d))
        self.assertEqual(req.call_count, 2)              # one for facts, one for submissions; submissions cached after

    def test_refresh_forces_a_new_request(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(S, "_request", return_value={"ok": True}) as req:
            S.fetch_company_facts("AMD", cache_dir=Path(d))
            S.fetch_company_facts("AMD", cache_dir=Path(d), refresh=True)
        self.assertEqual(req.call_count, 2)

    def test_unknown_symbol_raises_a_clear_error_not_a_network_call(self):
        with mock.patch.object(S, "_request") as req:
            with self.assertRaises(S.SECEdgarError):
                S.fetch_company_facts("ZZZZNOTREAL")
        req.assert_not_called()


class Contact(unittest.TestCase):
    def test_env_var_is_used_when_set(self):
        with mock.patch.dict("os.environ", {"SEC_EDGAR_CONTACT": "Test User test@example.com"}):
            self.assertEqual(S._contact(), "Test User test@example.com")

    def test_falls_back_to_a_generic_placeholder_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIn("SEC_EDGAR_CONTACT", S._contact())

    def test_explicit_argument_wins_over_the_env_var(self):
        with mock.patch.dict("os.environ", {"SEC_EDGAR_CONTACT": "env value"}):
            self.assertEqual(S._contact("explicit value"), "explicit value")


if __name__ == "__main__":
    unittest.main()

"""
Run from the project folder:   py -m unittest discover -s tests -v
Calendar quarter in, company quarter out (periods.py).

The rule under test: "2026q2" is every company's quarter that MOSTLY happened in April-June, i.e. whose middle month
lies in it (Nov-Jan is Q4, Feb-Apr is Q1, May-Jul is Q2, Aug-Oct is Q3 for a company whose year ends in July).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import periods as P                                            # noqa: E402

CALS = {
    "CPRT": P.FiscalCalendar(7, "end", "test"),
    "AAPL": P.FiscalCalendar(9, "end", "test"),
    "WMT": P.FiscalCalendar(1, "end", "test"),
    "TGT": P.FiscalCalendar(1, "start", "test"),
    "AMD": P.FiscalCalendar(12, "end", "test"),
}


def rc(symbol, text):
    return P.resolve(symbol, text, calendars=CALS, use_library=False)


class ParseQuarter(unittest.TestCase):
    def test_accepted_spellings(self):
        for text in ("2026Q2", "2026q2", "2026-Q2", "2026_q2", "2026 Q2", " 2026Q2 ", "Q2 2026", "q2-2026", "Q2_2026"):
            self.assertEqual(P.parse_quarter(text), (2026, 2), text)

    def test_rejected_spellings(self):
        for text in ("2026Q5", "2026Q0", "26Q2", "2026", "Q2", "second quarter", "", "2026Q22"):
            with self.assertRaises(ValueError, msg=text):
                P.parse_quarter(text)


class CalendarToCompany(unittest.TestCase):
    def test_copart_year_ends_july(self):
        # (calendar input) -> (fiscal year, fiscal quarter, last month of the company's quarter)
        cases = {"2025Q2": (2025, 4, "Jul 2025"),      # May-Jul 2025
                 "2025Q3": (2026, 1, "Oct 2025"),      # Aug-Oct 2025
                 "2025Q4": (2026, 2, "Jan 2026"),      # Nov 2025-Jan 2026 (Nov, Dec in Q4)
                 "2026Q1": (2026, 3, "Apr 2026"),      # Feb-Apr 2026 (Feb, Mar in Q1)
                 "2026Q2": (2026, 4, "Jul 2026"),      # May-Jul 2026 (May, Jun in Q2): the same season as AMD's Q2
                 "2026q3": (2027, 1, "Oct 2026")}
        for text, (fy, fq, end) in cases.items():
            cq = rc("CPRT", text)
            self.assertEqual((cq.fiscal_year, cq.fiscal_quarter, cq.period_end_label), (fy, fq, end), text)

    def test_copart_matches_what_alpha_vantage_returned(self):
        # verified 2026-09-21: Alpha Vantage 2026Q2 for CPRT was the "Second Quarter Fiscal 2026" call (Nov 2025-Jan 2026),
        # reported in February 2026: calendar 2025Q4 on the assumed Jan-Dec calendar.
        cq = rc("CPRT", "2025Q4")
        self.assertEqual(cq.fiscal_label, "2026Q2")
        self.assertEqual(P.from_fiscal("CPRT", "2026Q2", calendars=CALS, use_library=False).calendar_label, "2025Q4")

    def test_same_label_same_season_for_offcycle_and_calendar_companies(self):
        # "2026q2" is the Apr-Jun quarter for AMD and, mostly, the May-Jul quarter for Copart
        self.assertEqual(rc("AMD", "2026q2").period_end_label, "Jun 2026")
        self.assertEqual(rc("CPRT", "2026q2").period_end_label, "Jul 2026")

    def test_apple_year_ends_september(self):
        cq = rc("AAPL", "2026q2")                                   # the June quarter is Apple's fiscal Q3
        self.assertEqual((cq.fiscal_year, cq.fiscal_quarter, cq.period_end_label), (2026, 3, "Jun 2026"))
        self.assertEqual(rc("AAPL", "2025Q4").fiscal_label, "2026Q1")   # Oct-Dec 2025 opens fiscal 2026

    def test_calendar_year_company_is_unchanged(self):
        for q in range(1, 5):
            cq = rc("AMD", f"2026Q{q}")
            self.assertEqual((cq.fiscal_year, cq.fiscal_quarter), (2026, q))
            self.assertFalse(cq.differs)

    def test_january_year_end_puts_the_holiday_quarter_in_q4(self):
        # Nov-Jan is calendar Q4 for a January year-end, so Walmart's / Target's holiday quarter is 2025q4
        self.assertEqual(rc("WMT", "2025Q4").period_end_label, "Jan 2026")
        self.assertEqual(rc("TGT", "2025Q4").period_end_label, "Jan 2026")

    def test_naming_end_vs_start(self):
        # May-Jul 2026 is the second quarter of the year that ends Jan 2027
        self.assertEqual(rc("WMT", "2026Q2").fiscal_label, "2027Q2")      # named for the year it ends in
        self.assertEqual(rc("TGT", "2026Q2").fiscal_label, "2026Q2")      # named for the year it starts in
        self.assertEqual(rc("WMT", "2025Q4").fiscal_label, "2026Q4")      # holiday quarter of the year ending Jan 2026
        self.assertEqual(rc("TGT", "2025Q4").fiscal_label, "2025Q4")      # ... which Target calls fiscal 2025
        self.assertEqual(rc("TGT", "2026Q1").fiscal_label, "2026Q1")      # Feb-Apr 2026 opens Target's fiscal 2026

    def test_unknown_symbol_assumes_calendar_year_and_says_so(self):
        cq = rc("ZZZZ", "2026Q2")
        self.assertEqual(cq.fiscal_label, "2026Q2")
        self.assertEqual(cq.source, P.ASSUMED)


class RoundTrip(unittest.TestCase):
    def test_every_year_end_month_and_naming_is_one_to_one(self):
        """Whatever month the year ends in, each calendar quarter maps to exactly one company quarter and back, and the
        company quarter's MIDDLE month is inside the calendar quarter asked for."""
        for m in range(1, 13):
            for naming in ("end", "start"):
                cal = P.FiscalCalendar(m, naming, "test")
                seen = set()
                for year in range(2018, 2031):
                    for q in range(1, 5):
                        cq = P.resolve("X", f"{year}Q{q}", calendars={"X": cal}, use_library=False)
                        self.assertTrue(1 <= cq.fiscal_quarter <= 4)
                        end = cq.period_end_year * 12 + cq.period_end_month - 1
                        middle = end - 1
                        self.assertEqual((middle // 12, (middle % 12) // 3 + 1), (year, q),
                                         f"middle month of the quarter must lie in {year}Q{q} (m={m}, {naming})")
                        self.assertEqual((cq.period_end_month - m) % 3, 0, "quarter end must be a fiscal quarter-end month")
                        self.assertNotIn(cq.fiscal_label, seen, f"two calendar quarters gave {cq.fiscal_label} (m={m},{naming})")
                        seen.add(cq.fiscal_label)
                        back = P.from_fiscal("X", cq.fiscal_label, calendars={"X": cal}, use_library=False)
                        self.assertEqual(back.calendar_label, f"{year}Q{q}", (m, naming, year, q))
                        self.assertEqual(back.period_end_label, cq.period_end_label)

    def test_a_straddling_quarter_goes_to_the_calendar_quarter_holding_two_thirds_of_its_months(self):
        for m in range(1, 13):
            cal = P.FiscalCalendar(m, "end", "test")
            for year in (2025, 2026):
                for q in range(1, 5):
                    cq = P.resolve("X", f"{year}Q{q}", calendars={"X": cal}, use_library=False)
                    end = cq.period_end_year * 12 + cq.period_end_month - 1
                    in_quarter = sum(1 for idx in (end - 2, end - 1, end)
                                     if (idx // 12, (idx % 12) // 3 + 1) == (year, q))
                    self.assertGreaterEqual(in_quarter, 2, (m, year, q))

    def test_consecutive_calendar_quarters_are_consecutive_fiscal_quarters(self):
        cal = {"CPRT": CALS["CPRT"]}
        prev = None
        for year in range(2022, 2028):
            for q in range(1, 5):
                cq = P.resolve("CPRT", f"{year}Q{q}", calendars=cal, use_library=False)
                idx = cq.fiscal_year * 4 + cq.fiscal_quarter
                if prev is not None:
                    self.assertEqual(idx - prev, 1)
                prev = idx


class FromFiscal(unittest.TestCase):
    def test_apple_local_file_label(self):
        cq = P.from_fiscal("AAPL", "2026Q3", calendars=CALS, use_library=False)     # AAPL_Q3_2026.txt
        self.assertEqual((cq.calendar_label, cq.period_end_label), ("2026Q2", "Jun 2026"))

    def test_attach_period_fills_meta_without_overwriting(self):
        meta = {"ticker": "CPRT", "fiscal_year": 2026, "fiscal_quarter": 2}
        P.attach_period(meta, calendars=CALS, use_library=False)
        self.assertEqual((meta["calendar_label"], meta["period_end"], meta["fye_month"]), ("2025Q4", "2026-01", 7))
        meta2 = {"ticker": "CPRT", "fiscal_year": 2026, "fiscal_quarter": 2, "calendar_label": "kept"}
        P.attach_period(meta2, calendars=CALS, use_library=False)
        self.assertEqual(meta2["calendar_label"], "kept")

    def test_attach_period_needs_a_full_label(self):
        self.assertIsNone(P.attach_period({"ticker": "CPRT"}, calendars=CALS, use_library=False))

    def test_title_suffix_only_when_the_quarter_is_not_the_plain_calendar_one(self):
        meta = {"ticker": "CPRT", "fiscal_year": 2026, "fiscal_quarter": 2}
        P.attach_period(meta, calendars=CALS, use_library=False)
        self.assertEqual(P.title_suffix(meta), " (calendar 2025Q4; quarter ended Jan 2026)")
        amd = {"ticker": "AMD", "fiscal_year": 2026, "fiscal_quarter": 2}
        P.attach_period(amd, calendars=CALS, use_library=False)
        self.assertEqual(P.title_suffix(amd), "")
        # same label as the calendar quarter, but it ends in January: the reader must be told
        tgt = {"ticker": "TGT", "fiscal_year": 2025, "fiscal_quarter": 4}
        P.attach_period(tgt, calendars=CALS, use_library=False)
        self.assertEqual(P.title_suffix(tgt), " (calendar 2025Q4; quarter ended Jan 2026)")


class CallPeriodCheck(unittest.TestCase):
    COPART = ("Good day, everyone, and welcome to the Copart, Inc. Second Quarter Fiscal 2026 Earnings Call. "
              "Risk Factors in the annual report on Form 10-K for the year ended July 31, 2025.")

    def test_passes_when_the_call_matches(self):
        r = P.check_call_period(self.COPART, rc("CPRT", "2025Q4"))
        self.assertEqual(r["status"], "pass", r)
        r = P.check_call_period("welcome to the AMD Second Quarter 2026 Conference Call", rc("AMD", "2026Q2"))
        self.assertEqual(r["status"], "pass", r)

    def test_warns_when_the_quarter_is_wrong(self):
        r = P.check_call_period(self.COPART, rc("CPRT", "2026Q2"))          # fiscal Q4 expected, call says Q2
        self.assertEqual(r["status"], "warn")
        self.assertIn("fiscal calendar", r["detail"])

    def test_warns_when_a_calendar_year_company_is_really_off_cycle(self):
        # an unknown symbol falls back to the calendar year; the call reveals it is not
        r = P.check_call_period(self.COPART, rc("ZZZZ", "2025Q4"))
        self.assertEqual(r["status"], "warn")

    def test_warns_on_year_naming_mismatch(self):
        # right quarter (Target's fiscal Q2 = May-Jul), year off by one: the naming convention on file is probably wrong
        r = P.check_call_period("Welcome to the second quarter fiscal 2025 call", rc("TGT", "2026Q2"))
        self.assertEqual(r["status"], "warn")
        self.assertIn("naming", r["detail"])

    def test_tag_style_and_unknown(self):
        self.assertEqual(P.check_call_period("Welcome to our Q3 FY26 call", rc("AAPL", "2026Q2"))["status"], "pass")
        self.assertEqual(P.check_call_period("Hello and welcome to the call.", rc("AAPL", "2026Q2"))["status"], "unknown")


class SeededCalendars(unittest.TestCase):
    def test_file_loads_and_has_the_verified_entries(self):
        cals = P.load_calendars()
        self.assertEqual(cals["CPRT"].fye_month, 7)
        self.assertEqual(cals["AAPL"].fye_month, 9)
        self.assertEqual(cals["TGT"].naming, "start")
        self.assertTrue(all(1 <= c.fye_month <= 12 and c.naming in ("end", "start") for c in cals.values()))


if __name__ == "__main__":
    unittest.main()

"""
Run from the project folder:   py -m unittest discover -s tests -v
The batch runner (batch.py): planning, quota protection, scoring from the cache, and the audit statistics.
"""

import contextlib
import io
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

import batch                                                   # noqa: E402
import fetchers                                                # noqa: E402

TODAY = date(2026, 9, 21)


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class Planning(unittest.TestCase):
    def test_done_calls_are_skipped_unless_rescoring(self):
        self.assertEqual(batch.decide({"status": "ok"}, True, 5, TODAY)[0], "skip")
        self.assertEqual(batch.decide({"status": "ok"}, True, 5, TODAY, rescore=True)[0], "process")

    def test_cached_calls_never_cost_a_fetch_even_with_no_budget(self):
        self.assertEqual(batch.decide({}, True, 0, TODAY)[0], "process")
        self.assertEqual(batch.decide({"status": "error"}, True, 0, TODAY)[0], "process")

    def test_new_calls_need_budget(self):
        self.assertEqual(batch.decide({}, False, 3, TODAY)[0], "fetch")
        self.assertEqual(batch.decide({}, False, 0, TODAY)[0], "skip")

    def test_missing_transcripts_are_not_asked_for_again_too_soon(self):
        entry = {"status": "no_transcript", "last_attempt": "2026-09-20"}
        self.assertEqual(batch.decide(entry, False, 5, TODAY, retry_after_days=2)[0], "skip")
        self.assertEqual(batch.decide(entry, False, 5, date(2026, 9, 22), retry_after_days=2)[0], "fetch")

    def test_rate_limit_waits_for_tomorrow(self):
        entry = {"status": "rate_limited", "last_attempt": "2026-09-21"}
        self.assertEqual(batch.decide(entry, False, 5, TODAY)[0], "skip")
        self.assertEqual(batch.decide(entry, False, 5, date(2026, 9, 22))[0], "fetch")

    def test_error_classification(self):
        c = batch.classify_av_error
        self.assertEqual(c("Alpha Vantage: Our standard API rate limit is 25 requests per day."), "rate_limited")
        self.assertEqual(c("No API key. Set the ALPHAVANTAGE_API_KEY environment variable"), "fatal")
        self.assertEqual(c("Alpha Vantage: the parameter apikey is invalid or missing"), "fatal")
        self.assertEqual(c("Alpha Vantage request failed (ConnectionError): timed out"), "error")


class Symbols(unittest.TestCase):
    def test_list_file_and_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "w.txt"
            f.write_text("# my list\ncprt, vici  # comment\n\nHESM\nCPRT\n", encoding="utf-8")
            self.assertEqual(batch.load_symbols("amd", str(f)), ["AMD", "CPRT", "VICI", "HESM"])

    def test_from_securities_only_common_stocks_with_offset_and_limit(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "securities.csv"
            f.write_text("symbol,name,type\nA,a,Common Stock\nETF1,e,ETF\nB,b,Common Stock\nC,c,Common Stock\n", encoding="utf-8")
            self.assertEqual(batch.load_symbols(from_securities=True, securities_csv=f), ["A", "B", "C"])
            self.assertEqual(batch.load_symbols(from_securities=True, securities_csv=f, offset=1, limit=1), ["B"])


class AuditStatistics(unittest.TestCase):
    def test_zero_wrong_in_100_is_about_three_percent(self):
        self.assertAlmostEqual(batch.upper_error_bound(100, 0), 1 - 0.05 ** (1 / 100), places=6)      # ~2.95%
        self.assertLess(batch.upper_error_bound(100, 0), 0.03)

    def test_bound_shrinks_with_more_checks_and_grows_with_errors(self):
        self.assertLess(batch.upper_error_bound(200, 0), batch.upper_error_bound(100, 0))
        self.assertGreater(batch.upper_error_bound(100, 3), batch.upper_error_bound(100, 0))
        self.assertGreater(batch.upper_error_bound(50, 2), 2 / 50)          # always above the observed rate
        self.assertEqual(batch.upper_error_bound(0, 0), 1.0)


class EndToEndFromTheCache(unittest.TestCase):
    """Runs the real pipeline on AMD's cached call (which has gold) with the network switched off."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.no_network = mock.patch.object(fetchers, "_request", side_effect=AssertionError("network used"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def run_batch(self, symbols, season="2026q2", **kw):
        return quiet(batch.run, symbols, season, batch_root=self.root, today=TODAY, pause=0, **kw)

    def test_scores_a_cached_call_and_writes_the_files(self):
        self.assertEqual(self.run_batch(["AMD"]), 0)
        folder = self.root / "2026Q2"
        for name in ("scorecard.csv", "scorecard.json", "review_queue.csv", "audit_sample.csv", "summary.md", "state.json"):
            self.assertTrue((folder / name).exists(), name)
        row = json.loads((folder / "scorecard.json").read_text(encoding="utf-8"))[0]
        self.assertEqual((row["symbol"], row["status"], row["fiscal_label"], row["source"]), ("AMD", "ok", "2026Q2", "cache"))
        self.assertEqual(row["period_check"], "pass")
        self.assertEqual(row["unverified"], 0)
        self.assertEqual((row["gold_facts"], row["gold_snapshot"]), ("pass", "pass"))
        self.assertTrue(row["db_eligible"])
        self.assertGreater(row["facts"], 30)
        self.assertTrue((self.root / "scorecard_all.csv").exists())

    def test_second_run_skips_finished_calls_and_keeps_audit_verdicts(self):
        self.run_batch(["AMD"])
        audit = self.root / "2026Q2" / "audit_sample.csv"
        rows = batch._read_csv(audit)
        self.assertEqual(len(rows), 3)
        rows[0]["verdict"] = "ok"
        batch._write_csv(audit, batch.AUDIT_COLUMNS, rows)
        self.run_batch(["AMD"])                                            # skipped as done
        self.run_batch(["AMD"], rescore=True)                              # reprocessed, must not resample
        after = batch._read_csv(audit)
        self.assertEqual([r["verdict"] for r in after], ["ok", "", ""])
        self.assertEqual(len(after), 3)

    def test_unfetchable_symbols_are_recorded_and_do_not_stop_the_others(self):
        # ZZZZ has no cache and cache-only means a zero fetch budget
        self.run_batch(["ZZZZ", "AMD"], max_fetches=0)
        rows = {r["symbol"]: r for r in json.loads((self.root / "2026Q2" / "scorecard.json").read_text(encoding="utf-8"))}
        self.assertEqual(rows["AMD"]["status"], "ok")
        self.assertNotIn("ZZZZ", rows)                                     # skipped, never attempted, so not in the state

    def test_a_fetch_that_raises_is_recorded_as_a_status_and_the_run_continues(self):
        with mock.patch.object(batch, "fetch_from_alphavantage",
                               side_effect=[fetchers.NoTranscriptError("No transcript returned for QQQQ 2026Q2."),
                                            RuntimeError("boom")]):
            self.run_batch(["QQQQ", "WWWW"], max_fetches=5)
        state = json.loads((self.root / "2026Q2" / "state.json").read_text(encoding="utf-8"))["symbols"]
        self.assertEqual(state["QQQQ"]["status"], "no_transcript")
        self.assertEqual(state["WWWW"]["status"], "error")
        self.assertIn("RuntimeError", state["WWWW"]["detail"])
        rows = {r["symbol"]: r for r in json.loads((self.root / "2026Q2" / "scorecard.json").read_text(encoding="utf-8"))}
        self.assertFalse(rows["QQQQ"]["db_eligible"])

    def test_rate_limit_stops_further_fetches_but_not_cached_calls(self):
        with mock.patch.object(batch, "fetch_from_alphavantage",
                               side_effect=fetchers.AlphaVantageError("Alpha Vantage: rate limit is 25 requests per day")) as m:
            self.run_batch(["QQQQ", "WWWW"], max_fetches=5)
        self.assertEqual(m.call_count, 1)                                  # WWWW was not even tried
        state = json.loads((self.root / "2026Q2" / "state.json").read_text(encoding="utf-8"))["symbols"]
        self.assertEqual(state["QQQQ"]["status"], "rate_limited")
        self.assertNotIn("WWWW", state)

    def test_dry_run_writes_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            batch.run(["AMD", "CPRT"], "2026q2", dry_run=True, batch_root=self.root, today=TODAY)
        self.assertEqual(list(self.root.glob("**/*")), [])
        text = out.getvalue()
        self.assertIn("AMD", text)
        self.assertIn("Alpha Vantage 2026Q4", text)                         # CPRT's May-Jul quarter
        self.assertIn("Nothing was fetched or written", text)


class Safety(unittest.TestCase):
    def test_batch_never_touches_the_database(self):
        source = (ROOT / "batch.py").read_text(encoding="utf-8").lower()
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"')))
        self.assertNotIn("import duckdb", code)
        self.assertNotIn(".duckdb", code)


if __name__ == "__main__":
    unittest.main()

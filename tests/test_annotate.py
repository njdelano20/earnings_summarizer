"""
Run from the project folder:   py -m unittest discover -s tests -v
The annotated transcript page (annotate.py): unchecked vs checked against an answer key.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import annotate                                                # noqa: E402
import facts as F                                              # noqa: E402
from transcript import parse_transcript                        # noqa: E402

TEXT = ("Jane Roe: Revenue was a record $6.7 billion, up 16% sequentially. Gross margin was 56%. "
        "Operating margin was 27%. We hired 300 people.\n\n"
        "Bob Analyst: What about the $999 million charge?\n")


def build():
    t = parse_transcript(TEXT, "ABC_Q1_2025.txt")
    return t, F.extract_facts(t)


class Unchecked(unittest.TestCase):
    def test_highlights_captured_and_uncaptured_figures_without_judging_them(self):
        t, result = build()
        page = annotate.render_page(t, result)
        self.assertIn("UNCHECKED", page)
        self.assertNotIn("&#10003;", page)                      # no tick
        self.assertNotIn("&#10007;", page)                      # no cross
        self.assertIn("<mark", page)
        self.assertIn("$6.7 billion", page)
        self.assertIn('class="unc"', page)                      # "300" is not attached to any metric

    def test_page_escapes_transcript_text(self):
        t = parse_transcript("Jane Roe: Revenue was $5 billion <script>alert(1)</script> & more.", "ABC_Q1_2025.txt")
        page = annotate.render_page(t, F.extract_facts(t))
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)


class Checked(unittest.TestCase):
    def test_forbidden_facts_are_struck_and_missing_facts_are_added(self):
        t, result = build()
        gold = {"expected": [
                    {"kind": "reported", "metric": "gross_margin", "segment": "total", "stat": "level", "unit": "pct", "value": 56},
                    {"kind": "reported", "metric": "revenue", "segment": "total", "stat": "level", "unit": "USD",
                     "value": 11.5e9, "where": "Revenue was a record", "note": "the call says $11.5B elsewhere"}],
                "forbidden": [{"metric": "revenue", "segment": "total", "stat": "level", "value": 6.7e9,
                               "note": "that is Data Center revenue"}]}
        page = annotate.render_page(t, result, gold)
        self.assertIn("CHECKED", page)
        self.assertIn("&#10003;", page)                         # gross margin confirmed
        self.assertIn('class="wrong"', page)                    # the $6.7B mark is struck through
        self.assertIn("that is Data Center revenue", page)
        self.assertIn("+ MISSED", page)                         # placed at its `where` phrase
        self.assertIn("Changes the check makes", page)
        self.assertIn("1</b> missed", page)

    def test_statuses(self):
        t, result = build()
        gold = {"expected": [{"metric": "gross_margin", "segment": "total", "stat": "level", "value": 56}],
                "forbidden": [{"metric": "revenue", "segment": "total", "stat": "level", "value": 6.7e9}]}
        status, missed = annotate.check_against_gold(result["facts"], gold)
        kinds = {v[0] for v in status.values()}
        self.assertEqual(missed, [])
        self.assertIn("ok", kinds)
        self.assertIn("wrong", kinds)
        self.assertIn("unlisted", kinds)                        # operating margin is in neither list

    def test_gold_entry_reads_as_words(self):
        text = annotate.gold_entry_text({"kind": "reported", "metric": "revenue", "segment": "data center", "stat": "level",
                                         "unit": "USD", "value": 6.7e9, "change": {"value": 16, "unit": "pct", "basis": "qoq"}})
        self.assertIn("revenue / data center / level", text)
        self.assertIn("$6.7B", text)
        self.assertIn("16%/qoq", text)


class Files(unittest.TestCase):
    def test_write_page_names_the_two_states_differently(self):
        t, result = build()
        with tempfile.TemporaryDirectory() as d:
            a = annotate.write_page(t, result, Path(d))
            b = annotate.write_page(t, result, Path(d), gold={"expected": []}, label="TEST")
            self.assertTrue(a.name.endswith("_unchecked.html"))
            self.assertTrue(b.name.endswith("_checked_TEST.html"))
            self.assertTrue(a.exists() and b.exists())


if __name__ == "__main__":
    unittest.main()

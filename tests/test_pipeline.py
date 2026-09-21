"""
Run from the project folder:   py -m unittest discover -s tests -v
"""

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import facts as F                                           # noqa: E402
import fetchers                                              # noqa: E402
from evaluate import evaluate                                # noqa: E402
from parser import clean_text, split_into_sentences          # noqa: E402
from transcript import build_from_structured, parse_transcript  # noqa: E402


def facts_from(text: str, speaker: str = "Jane Roe") -> list[dict]:
    """Extract facts from a single management utterance."""
    t = parse_transcript(f"{speaker}: {text}", "ABC_Q1_2025.txt")
    return F.extract_facts(t)["facts"]


def one(facts, **want):
    hits = [f for f in facts if all(f.get(k) == v for k, v in want.items())]
    assert len(hits) == 1, f"expected exactly one fact matching {want}, got {len(hits)}: {[ (f['metric'], f['segment'], f['value']) for f in facts]}"
    return hits[0]


class SentenceSplitting(unittest.TestCase):
    def test_abbreviations_do_not_split(self):
        s = split_into_sentences("Growth in the U.S. Treasury market beat expectations vs. last year. Inc. results improved. "
                                 "Mr. Cook spoke.")
        self.assertEqual(len(s), 3)

    def test_us_before_any_capitalised_word_splits_unless_it_continues_a_name(self):
        # Copart: "...delivery services in the U.S. Operating income grew 2.8%..." was one sentence before this rule
        s = split_into_sentences("This includes long-haul delivery in the U.S. Operating income grew 2.8% to $464.3 million.")
        self.assertEqual(s[0], "This includes long-haul delivery in the U.S.")
        self.assertEqual(len(s), 2)
        self.assertEqual(len(split_into_sentences("Last month, U.S. News & World Report named Las Vegas first.")), 1)
        self.assertEqual(len(split_into_sentences("Property in the U.S. Virgin Islands is a U.S. territory.")), 1)
        self.assertEqual(len(split_into_sentences("Our U.S. insurance units declined 4.2%.")), 1)      # lower-case: an adjective

    def test_us_before_common_starter_does_split(self):
        self.assertEqual(len(split_into_sentences("We grew in the U.S. We also grew in Japan.")), 2)

    def test_lowercase_brand_names_start_sentences(self):
        self.assertEqual(len(split_into_sentences("Details for each of our revenue categories. iPhone revenue was $5 billion.")), 2)

    def test_decimals_do_not_split(self):
        self.assertEqual(len(split_into_sentences("EPS was $2.02 versus $1.98 last year.")), 1)

    def test_clean_text_normalizes_quotes_and_dashes(self):
        self.assertEqual(clean_text("it’s 9%–11%"), "it's 9%-11%")


class NumberParsing(unittest.TestCase):
    def test_number_words(self):
        self.assertEqual(F.words_to_number("two and a half"), 2.5)
        self.assertEqual(F.words_to_number("twenty-five"), 25)
        self.assertEqual(F.words_to_number("one"), 1)

    def test_no_trailing_period_captured(self):
        f = one(facts_from("We reported EPS of $2.02."), metric="eps")
        self.assertEqual(f["value"], 2.02)
        self.assertEqual(f["figure_text"], "$2.02")

    def test_money_scales_and_ranges(self):
        figs = F._find_figures("Between $1.5 and $2 billion, or $500 million, or $19.1 billion-$19.4 billion, or $1.2B")
        vals = [(f.lo, f.hi) for f in figs]
        self.assertIn((1.5e9, 2e9), vals)
        self.assertIn((500e6, None), vals)
        self.assertIn((19.1e9, 19.4e9), vals)
        self.assertIn((1.2e9, None), vals)

    def test_percent_and_bps_and_pp_words(self):
        figs = F._find_figures("Up 9%-11%, down 80 basis points, or about two and a half percentage points, or 47 to 48 percent")
        kinds = [(f.kind, f.lo, f.hi) for f in figs]
        self.assertIn(("pct", 9.0, 11.0), kinds)
        self.assertIn(("bps", 80.0, None), kinds)
        self.assertIn(("pp", 2.5, None), kinds)
        self.assertIn(("pct", 47.0, 48.0), kinds)


class MetricRules(unittest.TestCase):
    def test_level_with_yoy_change(self):
        f = one(facts_from("Revenue was $1.2 billion, up 8% year-over-year."), metric="revenue", stat="level")
        self.assertEqual((f["value"], f["segment"], f["kind"]), (1.2e9, "total", "reported"))
        self.assertEqual((f["changes"][0]["value"], f["changes"][0]["basis"]), (8.0, "yoy"))

    def test_negative_change_sequential(self):
        f = one(facts_from("Net income was $300 million, down 5% sequentially."), metric="net_income")
        self.assertEqual((f["changes"][0]["value"], f["changes"][0]["basis"]), (-5.0, "qoq"))

    def test_guidance_growth_range_and_period(self):
        f = one(facts_from("We expect revenue to grow between 5% and 7% for the year."), kind="guidance")
        self.assertEqual((f["metric"], f["stat"], f["value"], f["value_high"], f["period"]),
                         ("revenue", "growth", 5.0, 7.0, "full year"))

    def test_adjusted_eps_is_non_gaap_per_share(self):
        f = one(facts_from("Adjusted EPS was $1.25."), metric="eps")
        self.assertEqual((f["accounting"], f["unit"], f["value"]), ("non_gaap", "USD_per_share", 1.25))

    def test_capital_return_and_balance_sheet(self):
        fs = facts_from("We ended the quarter with $5 billion in cash and marketable securities and $2 billion "
                        "in total debt. We returned $1 billion to shareholders.")
        self.assertEqual(one(fs, metric="cash_and_securities")["value"], 5e9)
        self.assertEqual(one(fs, metric="total_debt")["value"], 2e9)
        self.assertEqual(one(fs, metric="capital_returned")["value"], 1e9)

    def test_constant_currency_growth_is_labelled(self):
        f = one(facts_from("Our revenue for the quarter was up 3% at constant currency."), metric="revenue")
        self.assertEqual(f["currency_basis"], "constant")

    def test_segment_from_metric_phrase_and_subject(self):
        fs = facts_from("Cloud revenue was $2 billion, up 10%. Transaction Processing, with its base of recurring "
                        "revenue, delivered revenue growth of 4%.")
        self.assertEqual(one(fs, metric="revenue", segment="cloud", stat="level")["value"], 2e9)
        self.assertEqual(one(fs, metric="revenue", segment="transaction processing")["value"], 4.0)

    def test_growth_of_a_named_entity_is_not_bound_to_total_revenue(self):
        t = parse_transcript("Jane Roe: Revenue grew, reflecting growth in Hybrid Infrastructure of 6%.", "ABC_Q1_2025.txt")
        r = F.extract_facts(t)
        self.assertFalse([f for f in r["facts"] if f["metric"] == "revenue" and f["value"] == 6.0])
        self.assertTrue([u for u in r["unclaimed"] if u["figure_text"] == "6%"])

    def test_a_figure_is_not_stolen_across_another_figure(self):
        fs = facts_from("This reflects adjusted EBITDA up $200 million year-over-year and about $400 million from timing.")
        self.assertFalse([f for f in fs if f["metric"] == "ebitda" and f["value"] == 400e6])
        self.assertEqual(one(fs, metric="ebitda", stat="change")["value"], 200e6)

    def test_period_is_the_one_nearest_the_figure(self):
        f = one(facts_from("We look at the 49.3% gross margin for the March quarter compared to the June quarter."),
                metric="gross_margin")
        self.assertEqual(f["period"], "march quarter")

    def test_impact_needs_a_size_word_near_the_figure(self):
        fs = facts_from("Foreign exchange was a headwind of about two percentage points to growth. "
                        "The 9%-11% guidance reflects the foreign exchange impact I mentioned earlier.")
        impacts = [f for f in fs if f["metric"] == "impact"]
        self.assertEqual([(f["value"], f["unit"], f["driver"]) for f in impacts], [(2.0, "pp", "foreign exchange")])

    def test_qualitative_guidance_is_kept_as_descriptor(self):
        f = one(facts_from("We expect the growth rate for Services to be in the mid-teens."), kind="guidance")
        self.assertEqual((f["segment"], f["descriptor"]), ("services", "mid-teens"))


class RolesAndSections(unittest.TestCase):
    TEXT = ("IR Person: Welcome to the call. I am the Director of Investor Relations.\n\n"
            "CFO Person: Revenue was $9 billion.\n\n"
            "Operator: We will take our first question from Jane Analyst from Big Bank. Please go ahead.\n\n"
            "Jane Analyst: Was revenue really $8 billion, up 30%?\n\n"
            "CFO Person: No, revenue was $9 billion.")

    def test_analyst_speech_yields_no_facts(self):
        t = parse_transcript(self.TEXT, "ABC_Q1_2025.txt")
        roles = {s["name"]: s["role"] for s in t.meta["speakers"]}
        self.assertEqual(roles["Jane Analyst"], "analyst")
        self.assertEqual(roles["IR Person"], "ir")
        r = F.extract_facts(t)
        self.assertEqual({f["speaker"] for f in r["facts"]}, {"CFO Person"})
        self.assertFalse([f for f in r["facts"] if f["value"] in (8e9, 30.0)])

    def test_qa_section_starts_at_first_question(self):
        t = parse_transcript(self.TEXT, "ABC_Q1_2025.txt")
        self.assertEqual([x.section for x in t.turns], ["prepared", "prepared", "qa", "qa", "qa"])

    def test_structured_turns_use_title_for_roles(self):
        turns = [{"speaker": "Ann", "title": "CEO", "content": "Revenue was $5 billion.", "sentiment": "0.5"},
                 {"speaker": "Operator", "title": "Operator", "content": "Our first question comes from Bob.", "sentiment": "0"},
                 {"speaker": "Bob", "title": "Analyst", "content": "Is it $4 billion?", "sentiment": "0"}]
        t = build_from_structured(turns, "ABC_2025Q1.json")
        self.assertEqual([x.role for x in t.turns], ["management", "operator", "analyst"])
        self.assertEqual(t.meta["fiscal_year"], 2025)
        self.assertEqual(t.meta["fiscal_quarter"], 1)
        for turn in t.turns:
            for s in turn.sentences:
                self.assertEqual(t.text[s.start:s.end], s.text)

    def test_unlabelled_speaker_after_questions_is_unknown_not_management(self):
        text = self.TEXT + "\n\nMystery Person: Revenue was $9 billion."
        t = parse_transcript(text, "ABC_Q1_2025.txt")
        self.assertEqual({s["name"]: s["role"] for s in t.meta["speakers"]}["Mystery Person"], "unknown")


class VerificationAndChecks(unittest.TestCase):
    def test_tampered_fact_fails_verification(self):
        t = parse_transcript("Jane Roe: Revenue was $1.2 billion.", "ABC_Q1_2025.txt")
        f = F.Fact(**{**F.extract_facts(t)["facts"][0]})
        self.assertEqual(F.verify_fact(f, t.text), [])
        f.value = 9.9e9
        self.assertIn("verify:value_mismatch", F.verify_fact(f, t.text))
        f2 = F.Fact(**{**F.extract_facts(t)["facts"][0]})
        f2.sentence = "Revenue was $1.3 billion."
        self.assertIn("verify:offset_mismatch", F.verify_fact(f2, t.text))

    def test_conflicting_values_are_flagged_and_downgraded(self):
        fs = facts_from("Revenue was $1 billion. Later, revenue was $2 billion.")
        self.assertTrue(all("conflict_with_other_value" in f["flags"] and f["confidence"] == "low" for f in fs))

    def test_products_plus_services_check(self):
        t = parse_transcript("Jane Roe: Revenue was $10 billion. Products revenue was $7 billion. "
                             "Services revenue was $3 billion.", "ABC_Q1_2025.txt")
        checks = {c["name"].split("[")[0]: c["status"] for c in F.extract_facts(t)["checks"]}
        self.assertEqual(checks["products_plus_services"], "pass")
        t = parse_transcript("Jane Roe: Revenue was $10 billion. Products revenue was $7 billion. "
                             "Services revenue was $5 billion.", "ABC_Q1_2025.txt")
        checks = {c["name"].split("[")[0]: c["status"] for c in F.extract_facts(t)["checks"]}
        self.assertEqual(checks["products_plus_services"], "warn")


class AlphaVantageFetcher(unittest.TestCase):
    def _fake_get(self, payload):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return mock.patch("requests.get", return_value=resp)

    def test_information_body_on_http_200_raises(self):
        with self._fake_get({"Information": "rate limit reached"}):
            with self.assertRaises(fetchers.AlphaVantageError):
                fetchers._request("AAPL", "2026Q3", "key")

    def test_empty_transcript_raises_no_transcript(self):
        with self._fake_get({"symbol": "AAPL", "quarter": "2026Q3", "transcript": []}):
            with self.assertRaises(fetchers.NoTranscriptError):
                fetchers._request("AAPL", "2026Q3", "key")

    def test_quarter_format_is_validated_before_any_request(self):
        with self.assertRaises(ValueError):
            fetchers.fetch_from_alphavantage("AAPL", "Q3-2026", ROOT / "data" / "raw" / "alphavantage")

    def test_missing_key_message_is_clear(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(fetchers.AlphaVantageError) as cm:
                fetchers.fetch_from_alphavantage("ZZZZ", "2024Q1", ROOT / "data" / "raw" / "does_not_exist")
        self.assertIn("ALPHAVANTAGE_API_KEY", str(cm.exception))


class GoldSets(unittest.TestCase):
    """The quality gate: every gold file must reach full recall with zero forbidden facts."""

    def _check(self, name):
        path = ROOT / "gold" / name
        r = evaluate(path)
        self.assertEqual(r["missed"], [], f"missed expected facts: {r['missed']}")
        self.assertEqual(r["forbidden_hits"], [])
        self.assertEqual(r["unverified"], 0)
        self.assertEqual(r["checks_failed"], 0)

    def test_aapl_q3_2026(self):
        self._check("AAPL_Q3_2026.json")

    def test_ibm_2024q1(self):
        self._check("IBM_2024Q1.json")

    def test_every_gold_file_that_is_not_pending(self):
        from evaluate import pending_gold
        pending = pending_gold()
        checked = []
        for path in sorted((ROOT / "gold").glob("*.json")):
            if path.stem in pending or path.name == "PENDING.json":
                continue
            with self.subTest(gold=path.name):
                self._check(path.name)
            checked.append(path.stem)
        self.assertIn("AMD_2026Q2", checked)                    # AMD was not covered by the two named tests above


if __name__ == "__main__":
    unittest.main()

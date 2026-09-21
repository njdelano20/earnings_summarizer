"""
Run from the project folder:   py -m unittest discover -s tests -v
Behaviours added while generalising the extractor beyond AAPL / IBM / AMD (2026-09-21). Each test is one small
sentence pattern, independent of any company's transcript, so a later change cannot silently undo it.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import facts as F                                              # noqa: E402
import signals as S                                            # noqa: E402
from transcript import build_from_structured, parse_transcript  # noqa: E402


def facts_from(text: str, filename: str = "ABC_Q1_2025.txt") -> list[dict]:
    return F.extract_facts(parse_transcript(f"Jane Roe: {text}", filename))["facts"]


def find(facts, **want):
    return [f for f in facts if all(f.get(k) == v for k, v in want.items())]


class SegmentContext(unittest.TestCase):
    def test_us_heading_and_a_long_section(self):
        fs = facts_from("Turning to our U.S. segment. Total units declined 4.2%. Insurance volumes decreased 4.2%. "
                        "U.S. gross profit grew to $484.1 million, up 0.9%. Operating income was $390.4 million, "
                        "reflecting a 38.1% operating margin.")
        self.assertEqual(len(find(fs, metric="operating_income", value=390.4e6, segment="u.s.")), 1)
        self.assertEqual(len(find(fs, metric="operating_margin", value=38.1, segment="u.s.")), 1)
        self.assertEqual(find(fs, metric="gross_profit", value=484.1e6)[0]["segment"], "u.s.")

    def test_us_abbreviation_does_not_glue_sentences(self):
        fs = facts_from("This includes long-haul delivery in the U.S. Operating income grew 2.8% to $464.3 million.")
        f = find(fs, metric="operating_income", value=464.3e6)[0]
        self.assertEqual(f["segment"], "total")                 # not "u s": the sentence boundary is honoured
        self.assertEqual(f["changes"][0]["value"], 2.8)

    def test_generic_transition_ends_the_section(self):
        fs = facts_from("Turning to our Embedded segment. Revenue was $977 million. "
                        "Finally, turning to our capital structure. Operating income was $3.1 billion.")
        self.assertEqual(find(fs, metric="revenue", value=977e6)[0]["segment"], "embedded")
        self.assertEqual(find(fs, metric="operating_income", value=3.1e9)[0]["segment"], "total")

    def test_a_company_wide_sentence_does_not_inherit_the_section(self):
        fs = facts_from("Turning to our Embedded segment. Revenue was $977 million. Our operating margin was 27%.")
        self.assertEqual(find(fs, metric="operating_margin", value=27.0)[0]["segment"], "total")

    def test_outlook_sentence_does_not_inherit_a_segment(self):
        # Apple: "On iPhone, we expect ... . We expect gross margin to be between 47% and 48%."
        fs = facts_from("On iPhone, we expect revenue growth to be mid-teens. We expect gross margin to be between 47% and 48%.")
        self.assertEqual(find(fs, metric="gross_margin", kind="guidance")[0]["segment"], "total")

    def test_segment_named_in_the_clause_beats_the_remembered_one(self):
        fs = facts_from("Shifting to Purple Wave, GTV grew 25%. On revenue, the U.S. segment was essentially flat, down 0.4%.")
        self.assertEqual(find(fs, metric="revenue", value=-0.4)[0]["segment"], "u.s.")

    def test_second_metric_in_a_sentence_shares_the_segment(self):
        fs = facts_from("U.S. gross profit grew to $484.1 million, up 0.9%, and gross profit margin was 48.3%.")
        self.assertEqual(find(fs, metric="gross_margin", value=48.3)[0]["segment"], "u.s.")

    def test_lowercase_noun_is_not_passed_on_as_a_segment(self):
        # IBM: "our revenue and operating margin performance resulted in 7% growth in our adjusted EBITDA"
        fs = facts_from("Overall, the combination of our revenue and operating margin performance resulted in 7% growth "
                        "in our adjusted EBITDA.")
        self.assertEqual(find(fs, metric="ebitda", value=7.0)[0]["segment"], "total")

    def test_qualifiers_and_colons_are_not_segments(self):
        fs = facts_from("Total revenues, excluding pass-through revenues, increased by approximately $10 million, resulting in "
                        "segment revenue changes as follows: Gathering revenues increased by approximately $7 million.")
        self.assertEqual(find(fs, metric="revenue", value=10e6)[0]["segment"], "total")
        self.assertEqual(find(fs, metric="revenue", value=7e6)[0]["segment"], "gathering")


class Phrasing(unittest.TestCase):
    def test_reiterate_guidance_is_guidance(self):
        fs = facts_from("We reiterate our 2026 adjusted free cash flow guidance of $910 million to $960 million.")
        self.assertEqual(find(fs, metric="free_cash_flow")[0]["kind"], "guidance")

    def test_a_target_is_guidance_and_the_result_before_it_is_not(self):
        fs = facts_from("Our adjusted EBITDA margin was maintained at approximately 85%, above our 75% target.")
        self.assertEqual(find(fs, metric="ebitda_margin", value=85.0)[0]["kind"], "reported")
        self.assertEqual(find(fs, metric="ebitda_margin", value=75.0)[0]["kind"], "guidance")

    def test_or_chain_and_excluding_currency(self):
        fs = facts_from("International revenue grew 14.1% or 7.9% excluding the positive impact of foreign currency "
                        "fluctuations, to $234.2 million.")
        f = find(fs, metric="revenue", value=234.2e6)[0]
        self.assertEqual([(c["value"], c["constant_currency"]) for c in f["changes"]], [(14.1, False), (7.9, True)])

    def test_margin_level_after_its_change(self):
        fs = facts_from("Global gross profit increased 3.7% to $572.6 million, with global gross margins increasing "
                        "71 basis points to 46.3%.")
        f = find(fs, metric="gross_margin", value=46.3)[0]
        self.assertEqual((f["segment"], f["changes"][0]["value"], f["changes"][0]["unit"]), ("total", 71.0, "bps"))

    def test_repurchased_and_cash_and_equivalents(self):
        fs = facts_from("We have repurchased over 43.4 million shares for an aggregate amount of over $1.6 billion. "
                        "We ended the quarter with $4.2 billion in cash and equivalents and held-to-maturity securities.")
        self.assertEqual(len(find(fs, metric="share_repurchases", value=1.6e9)), 1)
        self.assertEqual(len(find(fs, metric="cash_and_securities", value=4.2e9)), 1)

    def test_or_a_growth_rate_restates_the_metric_before_it(self):
        fs = facts_from("We reiterate our adjusted free cash flow guidance of $910 million to $960 million or a 20% "
                        "increase year-over-year at the midpoint.")
        f = find(fs, metric="free_cash_flow", stat="growth")[0]
        self.assertEqual((f["value"], f["basis"], f["kind"]), (20.0, "yoy", "guidance"))

    def test_excess_free_cash_flow_is_its_own_metric(self):
        fs = facts_from("We expect adjusted free cash flow of between $910 million and $960 million and excess adjusted "
                        "free cash flow of approximately $280 million.")
        self.assertEqual(len(find(fs, metric="free_cash_flow")), 1)
        self.assertEqual(len(find(fs, metric="excess_free_cash_flow", value=280e6)), 1)
        self.assertNotIn("conflict_with_other_value", find(fs, metric="free_cash_flow")[0]["flags"])

    def test_a_statement_is_one_fact_with_its_changes(self):
        fs = facts_from("Net income was $300 million, down 5% sequentially.")
        self.assertEqual(len(find(fs, metric="net_income")), 1)


class VocabularyPacks(unittest.TestCase):
    def test_marketplace_pack_reads_units_and_asp(self):
        fs = facts_from("Average selling prices rose 4.6% and more than offset a modest decline in unit volumes of 2.4%. "
                        "Global insurance units were down 2.7%.", "CPRT_Q3_2026.txt")
        self.assertEqual(find(fs, metric="asp")[0]["value"], 4.6)
        self.assertEqual(find(fs, metric="units", value=-2.4)[0]["stat"], "growth")
        self.assertEqual(find(fs, metric="units", value=-2.7)[0]["segment"], "insurance")

    def test_reit_pack_reads_affo_and_its_per_share_restatement(self):
        fs = facts_from("AFFO per share was $0.62 for the quarter, an increase of 4.6%. AFFO for the year is expected to be "
                        "between $2.675 billion and $2.695 billion, or between $2.45 and $2.47 per diluted common share.",
                        "VICI_Q2_2026.txt")
        self.assertEqual(find(fs, metric="affo_per_share", kind="reported")[0]["unit"], "USD_per_share")
        self.assertEqual(find(fs, metric="affo", kind="guidance")[0]["value_high"], 2.695e9)
        ps = find(fs, metric="affo_per_share", kind="guidance")[0]
        self.assertEqual((ps["value"], ps["value_high"], ps["unit"]), (2.45, 2.47, "USD_per_share"))

    def test_a_ticker_without_a_pack_gets_the_core_vocabulary_only(self):
        fs = facts_from("Global insurance units were down 2.7%.", "ABC_Q1_2025.txt")
        self.assertEqual(find(fs, metric="units"), [])

    def test_the_vocabulary_is_restored_after_each_call(self):
        facts_from("Average selling prices rose 4.6%.", "CPRT_Q3_2026.txt")
        self.assertIs(F._V, F._CORE_VOCAB)
        self.assertEqual(find(facts_from("Average selling prices rose 4.6%.", "ABC_Q1_2025.txt"), metric="asp"), [])

    def test_packs_are_recorded_in_the_result_meta(self):
        result = F.extract_facts(parse_transcript("Jane Roe: Revenue was $1 billion.", "VICI_Q2_2026.txt"))
        self.assertEqual(result["meta"]["vocabulary_packs"], ["reit"])


class ConversationManagement(unittest.TestCase):
    AGENDA = ["Today, I will discuss our second quarter performance and outlook for the remainder of the year.",
              "I will begin by walking through our financial results for the quarter.",
              "In the next few minutes, you will hear from John Payne on our growth outlook and activities.",
              "With that, operator, please open the line for questions.",
              "I am glad you noticed the strong performance on our margin.",
              "And for the analysts on the call, we are especially grateful for your presence today.",
              "Thanks for your question, Bob.", "Fair question.", "Daniel, you are spot-on.",
              "A reconciliation of these measures to the most directly comparable GAAP measure is available on our website."]
    BUSINESS = ["Looking to the second half of the year, we are planning for a softer PC market as higher memory and "
                "component costs weigh on demand.",
                "We will provide an update on our guidance next quarter as visibility improves.",
                "We appreciate the strong support of our customers, which drove record bookings."]

    def test_agenda_and_pleasantries_are_recognised_in_any_wording(self):
        for s in self.AGENDA:
            self.assertTrue(S._DISCOURSE.search(s) or S._BOILERPLATE.search(s) or S._PROCEDURAL.search(s), s)

    def test_real_business_sentences_are_not(self):
        for s in self.BUSINESS:
            self.assertFalse(S._DISCOURSE.search(s) or S._BOILERPLATE.search(s) or S._PROCEDURAL.search(s), s)

    def test_a_transition_only_counts_when_it_is_short(self):
        self.assertTrue(S._TRANSITION.match("Turning to our Embedded segment.") and 5 <= S._TRANSITION_MAX_WORDS)
        long_sentence = self.BUSINESS[0]
        self.assertTrue(S._TRANSITION.match(long_sentence))
        self.assertGreater(len(long_sentence.split()), S._TRANSITION_MAX_WORDS)


class Roles(unittest.TestCase):
    def test_a_general_counsels_prepared_remarks_are_procedure_but_answers_are_not(self):
        turns = [
            {"speaker": "Operator", "title": "Operator", "content": "Welcome to the call."},
            {"speaker": "Samantha Gallagher", "title": "General Counsel",
             "content": "Some of our comments today will be forward-looking statements."},
            {"speaker": "Edward Pitoniak", "title": "Chief Executive Officer", "content": "Revenue was $1 billion."},
            {"speaker": "Operator", "title": "Operator", "content": "Our first question comes from Barry."},
            {"speaker": "Barry", "title": "Analyst (Truist)", "content": "How is the pipeline?"},
            {"speaker": "Samantha Gallagher", "title": "General Counsel", "content": "We review every lease each quarter."},
        ]
        t = build_from_structured(turns, "VICI_2026Q2.json")
        roles = [(x.speaker, x.section, x.role) for x in t.turns]
        self.assertEqual(roles[1], ("Samantha Gallagher", "prepared", "ir"))
        self.assertEqual(roles[5], ("Samantha Gallagher", "qa", "management"))


if __name__ == "__main__":
    unittest.main()

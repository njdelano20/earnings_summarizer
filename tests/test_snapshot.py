"""
Business-snapshot layer: signals, segment registry, trajectory, selection, and the gold gate.
Run from the project folder:   py -m unittest discover -s tests -v
"""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import facts as F                                               # noqa: E402
import signals as S                                             # noqa: E402
from evaluate import evaluate_snapshot                          # noqa: E402
from segments import load_registry                              # noqa: E402
from snapshot import _sim, _verdict, build_snapshot             # noqa: E402
from transcript import build_from_structured, parse_transcript  # noqa: E402


def signals_from(text: str, ticker: str = "AAPL", speaker: str = "Jane Roe") -> list[dict]:
    t = parse_transcript(f"{speaker}: {text}", f"{ticker}_Q1_2025.txt")
    fr = F.extract_facts(t)
    return S.extract_signals(t, load_registry(ticker, [f["segment"] for f in fr["facts"]]), fr["facts"])


def snapshot_from(text: str, ticker: str = "ABC") -> dict:
    t = parse_transcript(f"Jane Roe: {text}", f"{ticker}_Q1_2025.txt")
    return build_snapshot(t, F.extract_facts(t))


def sig(signals, contains):
    hits = [s for s in signals if contains.lower() in s["sentence"].lower()]
    assert len(hits) == 1, f"expected one signal containing {contains!r}, got {len(hits)}"
    return hits[0]


class DirectionAndTopics(unittest.TestCase):
    def test_multi_digit_percentage_counts_as_growth(self):
        # regression: "up 22%" used to fail a \d\b boundary and read as neutral
        s = sig(signals_from("iPhone revenue was $54.3 billion, up 22% from a year ago."), "iPhone revenue")
        self.assertEqual(s["direction"], "positive")
        self.assertEqual(s["topics"][0], "momentum")

    def test_concession_clause_is_not_the_news(self):
        s = sig(signals_from("Mac grew 29% from a year ago despite significant supply constraints."), "Mac grew")
        self.assertEqual(s["direction"], "positive")
        self.assertNotIn("pressure", s["topics"])

    def test_partially_offset_by_does_not_make_a_decline_mixed(self):
        s = sig(signals_from("Application Operations revenue declined, reflecting weakness in custom projects, "
                             "partially offset by strength in cloud offerings.", ticker="IBM"), "Application Operations")
        self.assertEqual(s["direction"], "negative")

    def test_negated_positive_cue_does_not_count(self):
        # "we didn't see acceleration" used to read as positive momentum because of the word 'acceleration'
        self.assertEqual(S._direction("We didn't see acceleration, we saw stabilization.")[0], "neutral")
        self.assertEqual(S._direction("We saw acceleration in the quarter.")[0], "positive")
        self.assertEqual(signals_from("On the consumption side we didn't see acceleration, we saw stabilization.", ticker="IBM"), [])

    def test_noun_growth_alone_is_not_good_news(self):
        s = sig(signals_from("We expect revenue to decline, driving about a 0.5 point impact to our overall growth.",
                             ticker="IBM"), "expect revenue to decline")
        self.assertEqual(s["direction"], "negative")
        self.assertEqual(s["horizon"], "forward")

    def test_share_loss_and_gain_are_competition(self):
        s = sig(signals_from("According to IDC, we gained share globally during the quarter."), "gained share")
        self.assertIn("competition", s["topics"])

    def test_launch_events_outrank_commentary(self):
        sigs = signals_from("We were excited to unveil the all-new Siri AI. The reviews from early users have been phenomenal.")
        self.assertGreater(sig(sigs, "unveil")["score"], sig(sigs, "reviews")["score"])

    def test_revision_language(self):
        self.assertEqual(sig(signals_from("We are holding our view on revenue and free cash flow for the year.",
                                          ticker="IBM"), "holding our view")["revision"], "maintained")
        self.assertEqual(sig(signals_from("We now see mid-single-digit revenue growth in Consulting for the year.",
                                          ticker="IBM"), "now see")["revision"], "revised")
        # 'raised prices' is a pricing action, not a raised outlook
        self.assertIsNone(sig(signals_from("We reluctantly raised prices because of memory costs.", ticker="AAPL"),
                              "raised prices")["revision"])

    def test_stance_is_the_speakers_wording(self):
        s = sig(signals_from("I have never been more confident that the best is yet to come."), "never been more")
        self.assertEqual(s["stance"], "confident")
        s = sig(signals_from("Beyond September, we're not providing any color at this point."), "not providing")
        self.assertEqual((s["stance"], s["withheld"]), ("cautious", True))


class WhoIsMined(unittest.TestCase):
    def test_analyst_operator_and_boilerplate_never_become_signals(self):
        turns = [
            {"speaker": "Ira Relations", "title": "Investor Relations",
             "content": "These statements involve risks and uncertainties that may cause actual results to differ materially."},
            {"speaker": "Op", "title": "Operator", "content": "Our first question comes from Amit Daryanani with Evercore."},
            {"speaker": "Amit Daryanani", "title": "Analyst",
             "content": "It looks like a 500 basis point deceleration versus the June quarter, is that right?"},
            {"speaker": "Amit Daryanani", "title": "Analyst",
             "content": "Your revenue growth is decelerating and the memory inflation seems to challenge your margins."},
            {"speaker": "Kevan Parekh", "title": "CFO",
             "content": "Thank you. We expect revenue growth to slow because supply constraints will increase significantly."},
        ]
        t = build_from_structured(turns, "ABC_2025Q1.json")
        signals = S.extract_signals(t, load_registry("ABC", []), F.extract_facts(t)["facts"])
        self.assertTrue(signals)
        self.assertTrue(all(s["speaker"] == "Kevan Parekh" for s in signals))
        self.assertTrue(all(s["verified"] for s in signals))

    def test_pleasantries_and_process_talk_are_skipped(self):
        sigs = signals_from("Thank you, Suhasini. Good afternoon, everyone. Let me talk about the constraints a little more.")
        self.assertEqual(sigs, [])


class SegmentAttribution(unittest.TestCase):
    def test_parent_name_inside_child_name_is_not_a_second_mention(self):
        s = sig(signals_from("Technology Consulting revenue was also up 3% with double-digit growth in cloud projects.",
                             ticker="IBM"), "Technology Consulting")
        self.assertEqual(s["segments"], ["technology_consulting"])
        self.assertEqual(s["home"], "technology_consulting")

    def test_sentence_listing_a_segments_parts_is_filed_under_the_segment(self):
        s = sig(signals_from("Software grew by 6% with growth across Hybrid Platform & Solutions and Transaction Processing.",
                             ticker="IBM"), "Software grew")
        self.assertEqual(s["home"], "software")

    def test_qualitative_claim_borrows_the_segment_from_a_nearby_sentence(self):
        sigs = signals_from("iPhone revenue was $54.3 billion, up 22% from a year ago. "
                            "We achieved records in every geographic segment. According to IDC, we gained share globally.")
        s = sig(sigs, "gained share")
        self.assertIn("iphone", s["segments"])
        self.assertIn("segment_from_context", s["flags"])
        self.assertEqual(s["confidence"], "medium")

    def test_company_wide_or_numeric_sentences_never_borrow_a_segment(self):
        sigs = signals_from("Services revenue was $30.7 billion, up 12% year-over-year. "
                            "Company gross margin was 50.1%, up 80 basis points sequentially.")
        self.assertEqual(sig(sigs, "Company gross margin")["segments"], [])
        self.assertIsNone(sig(sigs, "Company gross margin")["home"])

    def test_ledger_segment_strings_resolve_through_the_registry(self):
        reg = load_registry("AAPL")
        self.assertEqual(reg.node_for_fact_segment("wearables, home, and accessories"), "wearables")
        self.assertEqual(reg.node_for_fact_segment("total"), "total")
        self.assertIsNone(reg.node_for_fact_segment("something else"))


class Trajectory(unittest.TestCase):
    def test_verdict_compares_reported_with_the_guided_range(self):
        self.assertEqual(_verdict(16, 9, 11, None)[0], "down")
        self.assertEqual(_verdict(3, 4, 6, None)[0], "up")
        self.assertEqual(_verdict(6, 6, 7, None)[0], "flat")
        self.assertEqual(_verdict(None, None, None, "down")[0], "down")
        self.assertEqual(_verdict(None, None, None, None)[0], None)

    def test_reported_versus_guided_growth_from_a_call(self):
        snap = snapshot_from("Our revenue of $100 billion was up 16% year-over-year in the June quarter. "
                             "We expect our September quarter total company revenue to grow between 9%-11% year-over-year.")
        row = next(r for r in snap["trajectory"] if r["key"] == "total:revenue")
        self.assertEqual(row["direction"], "down")
        self.assertEqual(row["reported"]["value"], 16)
        self.assertEqual(row["guided"]["source"], "ledger")

    def test_qualitative_guidance_is_read_from_text_and_labelled(self):
        snap = snapshot_from("Our revenue growth was 3% in the first quarter. "
                             "We see full-year revenue growth in line with our mid-single-digit model, still prudently at the low end.")
        row = next(r for r in snap["trajectory"] if r["key"] == "total:revenue")
        self.assertEqual(row["direction"], "up")
        self.assertEqual(row["guided"]["source"], "signal_text")
        self.assertEqual(row["confidence"], "medium")
        self.assertIn("not yet in the ledger", row["note"])


class Selection(unittest.TestCase):
    def test_same_message_from_two_speakers_is_one_message(self):
        a = "Today, Apple is pleased to report $109.4 billion in revenue, up 16% from a year ago and a June quarter record."
        b = "Our revenue of $109.4 billion was up 16% year-over-year, a June quarter revenue record."
        self.assertGreaterEqual(_sim(a, b), 0.7)
        self.assertLess(_sim(a, "We expect gross margin to be between 47%-48%."), 0.7)

    def test_each_sentence_appears_once_and_nothing_low_confidence_is_shown(self):
        for gold in ("AAPL_Q3_2026", "IBM_2024Q1"):
            r = evaluate_snapshot(ROOT / "gold" / "snapshot" / f"{gold}.json")
            self.assertEqual(r["checks_failed"], 0)


class VerificationAndFallbacks(unittest.TestCase):
    def test_tampered_evidence_fails_verification(self):
        t = parse_transcript("Jane Roe: Mac revenue was $10.4 billion, up 29% year-over-year.", "AAPL_Q1_2025.txt")
        fr = F.extract_facts(t)
        signals = S.extract_signals(t, load_registry("AAPL", []), fr["facts"])
        good = copy.deepcopy(signals[0])
        self.assertTrue(good["verified"])
        bad = S.Signal(**{k: v for k, v in good.items()})
        bad.ev_start += 3
        self.assertIn("evidence_offsets_do_not_match_text", S.verify_signal(bad, t))
        bad = S.Signal(**{k: v for k, v in good.items()})
        bad.speaker = "Somebody Else"
        self.assertIn("speaker_mismatch", S.verify_signal(bad, t))

    def test_unknown_ticker_gets_an_auto_registry_and_a_warning(self):
        snap = snapshot_from("Widgets revenue was $5 billion, up 10% year-over-year.", ticker="ZZZ")
        self.assertTrue(snap["coverage"]["registry"].startswith("auto"))
        warn = next(c for c in snap["checks"] if c["name"] == "segment_registry")
        self.assertEqual(warn["status"], "warn")
        self.assertEqual(snap["stats"]["checks_failed"], 0)

    def test_low_confidence_facts_are_held_back_not_shown(self):
        snap = snapshot_from("Revenue was $5 billion, up 10% year-over-year.")
        shown = {i for b in snap["blocks"] for i in b["fact_ids"]} | set(snap["company"]["fact_ids"])
        self.assertTrue(shown.isdisjoint(snap["coverage"]["held_back_low_confidence_facts"]))


class LessonsFromTheAMDCall(unittest.TestCase):
    """AMD was the first call nothing had been tuned on; each of these was a real defect it exposed."""

    def facts_of(self, text):
        return F.extract_facts(parse_transcript(f"Jane Roe: {text}", "ABC_Q1_2025.txt"))

    def test_prior_year_comparator_is_not_growth_or_a_reported_value(self):
        r = self.facts_of("Data center revenue more than doubled and now represents 58% of total revenue, "
                          "up from 42% a year ago, reflecting the expanding scale.")
        self.assertEqual([f for f in r["facts"] if f["value"] in (42.0, 58.0)], [])
        self.assertIn(("42%", "prior_period_comparator"), [(u["figure_text"], u["reason"]) for u in r["unclaimed"]])

    def test_dollar_comparator_is_not_a_reported_fact(self):
        r = self.facts_of("Revenue was $11.5 billion, compared to $10.2 billion a year ago.")
        self.assertEqual([f["value"] for f in r["facts"]], [11.5e9])

    def test_growth_from_a_year_ago_is_unaffected(self):
        r = self.facts_of("Revenue was up 12% from a year ago.")
        self.assertEqual([(f["stat"], f["value"]) for f in r["facts"]], [("growth", 12.0)])

    def test_greater_than_is_kept_as_a_qualifier(self):
        r = self.facts_of("We now expect revenue to grow substantially above our prior target of greater than 35%.")
        self.assertEqual(r["facts"][0]["qualifier"], "greater than")

    def test_floor_and_ceiling_guidance_are_not_points(self):
        self.assertEqual(_verdict(42, 35, None, None)[0], "flat")      # 42% is inside "greater than 35%"
        self.assertEqual(_verdict(30, 35, None, None)[0], "up")
        self.assertEqual(_verdict(6, None, 5, None)[0], "down")

    def test_accelerator_is_not_acceleration(self):
        self.assertIsNone(S._DIR_UP.search("the data center AI accelerator market"))
        self.assertIsNotNone(S._DIR_UP.search("growth accelerating in 2027"))
        reg = load_registry("ZZZ", [])
        self.assertIsNone(S._guided("We now expect the data center AI accelerator market to grow more than 45% annually.", [], reg))

    def test_market_growth_is_not_company_guidance_but_revenue_growth_is(self):
        reg = load_registry("ZZZ", [])
        self.assertIsNotNone(S._guided("We expect revenue to grow in the mid-single digits for the full year.", [], reg))

    def test_junk_segment_names_do_not_become_business_areas(self):
        reg = load_registry("ZZZ", ["segment", "addition", "widgets"])
        self.assertEqual(reg.ids(), ["widgets"])

    def test_conflicting_total_revenue_is_flagged_not_hidden(self):
        from snapshot import _total_revenue_conflicts
        facts = [{"id": "X-001", "metric": "revenue", "segment": "total", "flags": ["conflict_with_other_value"]},
                 {"id": "X-002", "metric": "revenue", "segment": "total", "flags": []},
                 {"id": "X-003", "metric": "revenue", "segment": "mac", "flags": ["conflict_with_other_value"]}]
        self.assertEqual(_total_revenue_conflicts(facts), ["X-001"])


class GoldGate(unittest.TestCase):
    def test_snapshot_gold_files_pass(self):
        from evaluate import pending_gold
        pending = pending_gold()
        for gold in sorted((ROOT / "gold" / "snapshot").glob("*.json")):
            if gold.stem in pending:
                continue                                        # still being fixed: see gold/PENDING.json
            r = evaluate_snapshot(gold)
            self.assertEqual(r["bad_gold"], [], gold.name)
            self.assertEqual(r["missed"], [], gold.name)
            self.assertEqual(r["forbidden_hits"], [], gold.name)
            self.assertEqual(r["no_facts"] + r["no_comment"], [], gold.name)
            self.assertEqual(r["wrong_traj"], [], gold.name)
            self.assertEqual(r["not_surfaced"], [], gold.name)
            self.assertEqual((r["unverified"], r["checks_failed"]), (0, 0), gold.name)


if __name__ == "__main__":
    unittest.main()

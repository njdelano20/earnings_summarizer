"""
Run from the project folder:   py -m unittest discover -s tests -v
Industry-driven vocabulary pack resolution (vocab.py, vocabulary/symbol_industry.json + industry_packs.json).

These tests read the checked-in snapshot (vocabulary/symbol_industry.json, refreshed from the MarketDataLibrary by
refresh_industries.py) and use large, stable, well-known tickers whose GICS-style classification is very unlikely to
change, so a failure here means the resolution logic broke, not that the library was re-scraped. If one of these
starts failing after a legitimate `py refresh_industries.py`, update the expectation, don't just delete the test.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import vocab as V                                              # noqa: E402

HAS_SNAPSHOT = V.SYMBOL_INDUSTRY_FILE.exists()


@unittest.skipUnless(HAS_SNAPSHOT, "vocabulary/symbol_industry.json not present; run py refresh_industries.py")
class IndustryResolution(unittest.TestCase):
    def test_well_known_tickers_resolve_to_the_right_pack(self):
        cases = {"JPM": "bank", "AMD": "semiconductor_hardware", "NVDA": "semiconductor_hardware",
                 "UNH": "healthcare_services", "WMT": "retail", "HESM": "oil_gas_midstream"}
        for ticker, pack in cases.items():
            self.assertIn(pack, V.packs_for(ticker), ticker)

    def test_credit_services_covers_both_payment_networks_and_lenders(self):
        # Yahoo's 'Credit Services' industry genuinely mixes these; the pack must serve both
        for ticker in ("V", "MA", "PYPL", "COF", "SOFI"):
            self.assertIn("credit_finance", V.packs_for(ticker), ticker)

    def test_sector_fallback_catches_a_reit_with_no_industry_on_file(self):
        # VICI: sector 'Real Estate', industry blank in the library (a scraping gap, not a mapping bug)
        info = V.industry_of("VICI")
        self.assertIsNotNone(info)
        self.assertIsNone(info.get("industry"))
        self.assertEqual(info.get("sector"), "Real Estate")
        self.assertIn("reit", V.packs_for("VICI"))

    def test_ticker_override_and_industry_resolution_combine_without_duplicates(self):
        packs = V.packs_for("CPRT")
        self.assertEqual(packs, ["marketplace"])          # ticker override; Copart's own industry maps to nothing

    def test_unknown_ticker_and_unclassified_industry_get_no_packs(self):
        self.assertEqual(V.packs_for("ZZZZNOTREAL"), [])
        self.assertEqual(V.packs_for(None), [])

    def test_most_industries_are_intentionally_core_only(self):
        # a random sample of industries that should NOT have a pack: core vocabulary already covers them
        import json
        mapping = json.loads(V.INDUSTRY_PACKS_FILE.read_text(encoding="utf-8"))["industries"]
        for industry in ("Specialty Chemicals", "Aerospace & Defense", "Packaged Foods", "Waste Management"):
            self.assertNotIn(industry, mapping, industry)


class MappingConsistency(unittest.TestCase):
    """These do not need the snapshot: they only check the mapping and pack files are internally consistent."""

    def test_every_pack_referenced_in_the_mapping_has_a_file(self):
        import json
        mapping = json.loads(V.INDUSTRY_PACKS_FILE.read_text(encoding="utf-8"))
        referenced = {p for lst in mapping["industries"].values() for p in lst}
        referenced |= {p for lst in mapping.get("sectors", {}).values() if isinstance(lst, list) for p in lst}
        for pack in referenced:
            self.assertTrue((V.VOCAB_DIR / f"{pack}.json").exists(), pack)

    def test_every_pack_file_has_at_least_one_metric_with_a_valid_pattern(self):
        import json
        import re
        for path in V.VOCAB_DIR.glob("*.json"):
            if path.name in ("industry_packs.json", "tickers.json", "symbol_industry.json"):
                continue
            pack = json.loads(path.read_text(encoding="utf-8"))
            metrics = pack.get("metrics", [])
            self.assertTrue(metrics, path.name)
            for m in metrics:
                re.compile(rf"\b(?:{m['pattern']})\b", re.I)    # must not raise


if __name__ == "__main__":
    unittest.main()

"""
vocab.py
Industry vocabulary packs: the metrics a sector talks about that the core extractor does not know.

The core vocabulary in facts.py (revenue, margins, EPS, cash flow, ...) is the same for every company. Everything
sector-specific lives in vocabulary/<pack>.json as DATA, so a new sector is a new file, not a code change:

    {"name": "reit", "description": "...",
     "metrics": [{"name": "affo_per_share", "pattern": "affo[- ]per[- ](?:diluted\\s+)?share", "per_share": true},
                 {"name": "affo", "pattern": "affo", "per_share_metric": "affo_per_share"},
                 {"name": "occupancy", "pattern": "occupancy(?:\\s+rate)?", "pct_level": true}]}

A metric may say: "segmented" (its segment is read from the words before it, like revenue), "pct_level" (its figure
is a level in percent, like a margin, not a growth rate), "per_share" (a dollar figure is per share) and
"per_share_metric" (the name to use for a following "... $2.45 to $2.47 per share" that restates it). A metric whose
usual figure is a physical quantity (subscribers, barrels/day, megawatts, ...) still works for growth-rate mentions
("subscribers grew 5%") via the normal percent-growth mechanism; an ABSOLUTE level ("50 million subscribers") is not
captured yet -- that needs a "quantity" figure type the core extractor does not have. Packs say so in their
description so the gap is visible, not silent.

WHICH PACKS A TICKER GETS (packs_for -- resolved automatically, not hand-maintained per ticker):
  1. vocabulary/symbol_industry.json    every symbol's sector/industry, a snapshot of the MarketDataLibrary's own
                                        classification (Yahoo Finance's, already populated via the library's
                                        stockanalysis.com scraping). Refresh with `py refresh_industries.py`.
  2. vocabulary/industry_packs.json     maps each Yahoo industry to the pack(s) it uses. Most of the ~150 industries
                                        in the library map to nothing (core vocabulary already covers them, e.g.
                                        "Specialty Chemicals"): only industries whose REPORTING CONVENTION genuinely
                                        differs (banks, insurers, REITs, telecom, ...) get a pack.
  3. vocabulary/tickers.json            explicit per-ticker overrides, layered on top, for the rare case where a
                                        ticker's classification is missing, wrong, or the company spans categories.
A ticker with no industry on file and no override gets the core vocabulary only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

VOCAB_DIR = Path(__file__).resolve().parent / "vocabulary"
SYMBOL_INDUSTRY_FILE = VOCAB_DIR / "symbol_industry.json"
INDUSTRY_PACKS_FILE = VOCAB_DIR / "industry_packs.json"
TICKERS_FILE = VOCAB_DIR / "tickers.json"

_cache: dict[str, dict] = {}


@dataclass
class Vocabulary:
    triggers: list[tuple[str, re.Pattern]]
    segmented: set[str]
    pct_level: set[str]
    per_share_of: dict[str, str] = field(default_factory=dict)  # "affo" -> "affo_per_share"
    per_share_metrics: set[str] = field(default_factory=set)    # metrics whose dollar figure is per share
    packs: list[str] = field(default_factory=list)


def _load_json(path: Path, default):
    key = str(path)
    if key not in _cache:
        try:
            _cache[key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        except (OSError, ValueError):
            _cache[key] = default
    return _cache[key]


def _load_pack(name: str) -> dict:
    return json.loads((VOCAB_DIR / f"{name}.json").read_text(encoding="utf-8"))


def industry_of(ticker: str | None) -> dict | None:
    """{'sector': ..., 'industry': ...} for a ticker, from the symbol_industry.json snapshot, or None if the
    snapshot has never been refreshed or the ticker is not in the MarketDataLibrary."""
    if not ticker:
        return None
    return _load_json(SYMBOL_INDUSTRY_FILE, {}).get(ticker.strip().upper())


def packs_for(ticker: str | None) -> list[str]:
    """Which vocabulary packs a ticker uses: the industry-derived packs plus any explicit override, deduplicated,
    override first. A ticker with an unmapped industry, or none on file, may still get packs from an override."""
    if not ticker:
        return []
    ticker = ticker.strip().upper()
    packs: list[str] = list(_load_json(TICKERS_FILE, {}).get("tickers", {}).get(ticker, []))
    info = industry_of(ticker)
    if info:
        mapping = _load_json(INDUSTRY_PACKS_FILE, {})
        found = mapping.get("industries", {}).get(info.get("industry") or "", [])
        if not found:
            # the specific industry gave nothing (missing or unmapped): fall back to the symbol's sector, if that
            # sector has a safe default (industry_packs.json's "sectors" -- deliberately only for a few sectors)
            found = mapping.get("sectors", {}).get(info.get("sector") or "", [])
        packs += found
    seen, out = set(), []
    for p in packs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def build(core_triggers: list[tuple[str, re.Pattern]], core_segmented: set[str], core_pct_level: set[str],
          ticker: str | None) -> Vocabulary:
    """The core vocabulary plus the packs assigned to `ticker`."""
    vocab = Vocabulary(list(core_triggers), set(core_segmented), set(core_pct_level))
    for pack_name in packs_for(ticker):
        try:
            pack = _load_pack(pack_name)
        except (OSError, ValueError):
            continue
        vocab.packs.append(pack_name)
        for m in pack.get("metrics", []):
            vocab.triggers.append((m["name"], re.compile(rf"\b(?:{m['pattern']})\b", re.I)))
            if m.get("segmented"):
                vocab.segmented.add(m["name"])
            if m.get("pct_level"):
                vocab.pct_level.add(m["name"])
            if m.get("per_share"):
                vocab.per_share_metrics.add(m["name"])
            if m.get("per_share_metric"):
                vocab.per_share_of[m["name"]] = m["per_share_metric"]
    return vocab

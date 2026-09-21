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
"per_share_metric" (the name to use for a following "... $2.45 to $2.47 per share" that restates it).

vocabulary/tickers.json says which packs a ticker uses. A ticker with no entry gets the core vocabulary only, and the
batch scorecard flags it so nobody assumes a sector was covered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

VOCAB_DIR = Path(__file__).resolve().parent / "vocabulary"


@dataclass
class Vocabulary:
    triggers: list[tuple[str, re.Pattern]]
    segmented: set[str]
    pct_level: set[str]
    per_share_of: dict[str, str] = field(default_factory=dict)      # "affo" -> "affo_per_share"
    per_share_metrics: set[str] = field(default_factory=set)        # metrics whose dollar figure is per share
    packs: list[str] = field(default_factory=list)


def _load_pack(name: str) -> dict:
    path = VOCAB_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def packs_for(ticker: str | None) -> list[str]:
    path = VOCAB_DIR / "tickers.json"
    if not ticker or not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("tickers", {}).get(ticker.upper(), []))


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

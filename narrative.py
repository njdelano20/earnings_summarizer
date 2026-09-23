"""
narrative.py
Generates the four narrative sections (business-segment drivers, competitive environment,
product development, macro/regulatory) that were hand-written by reading the AAPL transcript
directly this session -- automated here so every gold-validated symbol-quarter gets all 8
sections, not just AAPL, with zero new fabrication risk: every sentence surfaced here is a real
transcript sentence, already topic-tagged and evidence-verified by signals.py and cached in
output/<STEM>_snapshot.json (`verified: true` on each one) -- nothing is paraphrased, summarized,
or synthesized by an LLM. This trades polish for safety: the output reads as curated real
quotes/paraphrase-free excerpts, not flowing analyst prose, but it can never invent a claim.

Topic -> section mapping (signals.py's own taxonomy, verified against AAPL's real, hand-checked
output before trusting it generically -- see project notes):
  - "competition" topic  -> Competitive environment
  - "product" topic      -> Product development (ranked by signals.py's own relevance `score`,
                            since a well-covered company can have 80+ product-topic sentences --
                            far too many to show all of; this picks the highest-scoring handful)
  - "pressure" topic, further filtered by real macro/regulatory keywords in the sentence itself
    (tariff, regulat*, foreign exchange/fx, supply chain, memory cost, inflation, rates) --
    the raw "pressure" topic alone is noisier, catching unrelated things like a segment's
    "difficult compare" explanation -> Macro / regulatory
  - a signal's own `segments` tag (already the same slug facts.py assigns, e.g. "iphone") ->
    that segment's driver bullet in Business segments

    py narrative.py AMD_2026Q2       print what would be generated, no file written
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"

_MACRO_KEYWORDS = re.compile(
    r"\btariff|regulat\w*|foreign exchange|\bfx\b|supply chain|memory cost|inflation|"
    r"interest rate|\bfed\b|federal reserve\b", re.I)

_MAX_PRODUCT_BULLETS = 6
_MAX_MACRO_BULLETS = 5
_MAX_COMPETITIVE_SENTENCES = 3
_MAX_PER_SEGMENT = 2


def load_signals(stem: str) -> list[dict] | None:
    path = OUTPUT_DIR / f"{stem}_snapshot.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("signals")


def _clean(sentence: str) -> str:
    return sentence.strip().rstrip(".") + "."


def segment_drivers(signals: list[dict], segments: list[dict]) -> dict[str, list[str]]:
    """{segment_display_name: [real sentence, ...]} -- only for segments that actually have a
    tagged signal; a segment with no real evidence sentence gets no entry, never a guess."""
    out = {}
    for seg in segments:
        slug = seg.get("slug", "")
        matches = [s for s in signals if slug in s.get("segments", []) and s.get("verified")]
        matches.sort(key=lambda s: -s.get("score", 0))
        if matches:
            out[seg["name"]] = [_clean(s["sentence"]) for s in matches[:_MAX_PER_SEGMENT]]
    return out


def competitive_environment(signals: list[dict]) -> list[str]:
    matches = [s for s in signals if "competition" in s.get("topics", []) and s.get("verified")]
    matches.sort(key=lambda s: -s.get("score", 0))
    return [_clean(s["sentence"]) for s in matches[:_MAX_COMPETITIVE_SENTENCES]]


_REVENUE_SENTENCE = re.compile(
    r"\$[\d.]+\s*(billion|million)\b.{0,40}\b(year-over-year|yoy|sequential(ly)?|down \d|up \d)", re.I)
_MACRO_BUCKETS = [
    ("tariff", re.compile(r"\btariff", re.I)),
    ("regulatory", re.compile(r"regulat\w*", re.I)),
    ("fx", re.compile(r"foreign exchange|\bfx\b", re.I)),
    ("supply", re.compile(r"supply chain|memory cost", re.I)),
    ("rates", re.compile(r"interest rate|\bfed\b|federal reserve", re.I)),
]


def _dedup_by_word_overlap(sentences: list[str], threshold: float = 0.55) -> list[str]:
    """Keeps a sentence only if it doesn't share >threshold of its significant words with one
    already kept -- catches near-restatements of the same point ('we expect FX to be a headwind
    of 2.5pp' said three different ways) that a plain string-prefix check misses."""
    kept: list[str] = []
    kept_wordsets: list[set[str]] = []
    for sent in sentences:
        words = {w for w in re.findall(r"[a-z]{4,}", sent.lower())}
        if not words:
            continue
        is_dup = False
        for kw in kept_wordsets:
            overlap = len(words & kw) / max(1, min(len(words), len(kw)))
            if overlap > threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(sent)
            kept_wordsets.append(words)
    return kept


def product_development(signals: list[dict]) -> list[str]:
    matches = [s for s in signals if "product" in s.get("topics", []) and s.get("verified")
              and not _REVENUE_SENTENCE.search(s["sentence"])]   # a revenue restatement isn't "development" news
    matches.sort(key=lambda s: -s.get("score", 0))
    candidates = [_clean(s["sentence"]) for s in matches]
    return _dedup_by_word_overlap(candidates)[:_MAX_PRODUCT_BULLETS]


def macro_regulatory(signals: list[dict]) -> list[str]:
    matches = [s for s in signals if "pressure" in s.get("topics", []) and s.get("verified")
              and _MACRO_KEYWORDS.search(s["sentence"])]
    matches.sort(key=lambda s: -s.get("score", 0))
    deduped = _dedup_by_word_overlap([_clean(s["sentence"]) for s in matches])
    # spread across the real macro themes present (tariff/regulatory/fx/supply/rates) rather than
    # letting whichever theme happens to score highest fill every slot -- one pass per bucket,
    # highest-scored survivor in each, before falling back to whatever's left
    by_bucket: dict[str, list[str]] = {}
    for sent in deduped:
        for name, rx in _MACRO_BUCKETS:
            if rx.search(sent):
                by_bucket.setdefault(name, []).append(sent)
                break
    out: list[str] = []
    for name, _ in _MACRO_BUCKETS:
        if by_bucket.get(name) and len(out) < _MAX_MACRO_BULLETS:
            out.append(by_bucket[name][0])
    for sent in deduped:
        if len(out) >= _MAX_MACRO_BULLETS:
            break
        if sent not in out:
            out.append(sent)
    return out


def build_narrative(stem: str, segments: list[dict]) -> dict:
    """Returns {} entirely if no cached snapshot exists (never fabricated) -- otherwise each of
    the 4 keys is present only when real evidence was actually found for it."""
    signals = load_signals(stem)
    if not signals:
        return {}
    out = {}
    drivers = segment_drivers(signals, segments)
    if drivers:
        out["segment_drivers"] = drivers
    comp = competitive_environment(signals)
    if comp:
        out["competitive_environment"] = comp
    prod = product_development(signals)
    if prod:
        out["product_development"] = prod
    macro = macro_regulatory(signals)
    if macro:
        out["macro_regulatory"] = macro
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: py narrative.py <STEM>", file=sys.stderr)
        return 1
    stem = sys.argv[1]
    signals = load_signals(stem)
    if signals is None:
        print(f"No cached snapshot for {stem} (expected output/{stem}_snapshot.json)", file=sys.stderr)
        return 1
    print("Competitive environment:")
    for s in competitive_environment(signals):
        print(" ", s)
    print("\nProduct development:")
    for s in product_development(signals):
        print(" ", s)
    print("\nMacro / regulatory:")
    for s in macro_regulatory(signals):
        print(" ", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())

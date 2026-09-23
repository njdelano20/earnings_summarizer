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
# a customer-story sentence ("[Some Bank] is using our AI to streamline regulatory workflows")
# can hit the "regulat*" keyword above by accident -- it's a product use-case, not the reporting
# company's own regulatory exposure. Real 2026-09-22 false positive on AAPL, caught by reading
# actual output, not by reasoning about the regex.
_CUSTOMER_STORY = re.compile(r"\bis using\b|\bare using\b|\bhas been using\b", re.I)

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


def segment_drivers(signals: list[dict], segments: list[dict], used: "_Used") -> dict[str, list[str]]:
    """{segment_display_name: [real sentence, ...]} -- only for segments that actually have a
    tagged signal; a segment with no real evidence sentence gets no entry, never a guess. A
    sentence tagged with multiple segments (e.g. one combined-guidance sentence covering client,
    embedded, and gaming together) is only ever claimed by the first (highest-revenue) segment
    it matches, not repeated under every segment it's tagged with."""
    out = {}
    for seg in segments:
        slug = seg.get("slug", "")
        matches = [s for s in signals if slug in s.get("segments", []) and s.get("verified")
                  and not _CUSTOMER_STORY.search(s["sentence"])]
        matches.sort(key=lambda s: -s.get("score", 0))
        picked = []
        for s in matches:
            if len(picked) >= _MAX_PER_SEGMENT:
                break
            sent = _clean(s["sentence"])
            if used.claim(sent):
                picked.append(sent)
        if picked:
            out[seg["name"]] = picked
    return out


def competitive_environment(signals: list[dict], used: "_Used") -> list[str]:
    matches = [s for s in signals if "competition" in s.get("topics", []) and s.get("verified")]
    matches.sort(key=lambda s: -s.get("score", 0))
    out = []
    for s in matches:
        if len(out) >= _MAX_COMPETITIVE_SENTENCES:
            break
        sent = _clean(s["sentence"])
        if used.claim(sent):
            out.append(sent)
    return out


_REVENUE_SENTENCE = re.compile(
    r"\$[\d.]+\s*(billion|million)\b.{0,40}\b(year-over-year|yoy|sequential(ly)?|down \d|up \d)", re.I)
_MACRO_BUCKETS = [
    ("tariff", re.compile(r"\btariff", re.I)),
    ("regulatory", re.compile(r"regulat\w*", re.I)),
    ("fx", re.compile(r"foreign exchange|\bfx\b", re.I)),
    ("supply", re.compile(r"supply chain|memory cost", re.I)),
    ("rates", re.compile(r"interest rate|\bfed\b|federal reserve", re.I)),
]


class _Used:
    """Tracks every sentence claimed by some section so far (across segment drivers,
    competitive environment, product development, and macro/regulatory), so the same real
    transcript sentence -- or a near-restatement of it -- never appears twice in one document.
    Real 2026-09-22 bug: AMD's top-scored, multi-segment-tagged guidance sentence was picked
    independently by 3 segment-driver bullets AND product development, since each section used
    to select its candidates with no awareness of what any other section had already used."""

    def __init__(self, threshold: float = 0.55) -> None:
        self._threshold = threshold
        self._keys: set[str] = set()
        self._wordsets: list[set[str]] = []

    def claim(self, sentence: str) -> bool:
        """Records `sentence` and returns True if it's new; returns False (does nothing) if it
        exactly or near-duplicates something already claimed by an earlier section."""
        key = re.sub(r"\s+", " ", sentence.strip().lower())
        if key in self._keys:
            return False
        words = {w for w in re.findall(r"[a-z]{4,}", sentence.lower())}
        if words:
            for kw in self._wordsets:
                overlap = len(words & kw) / max(1, min(len(words), len(kw)))
                if overlap > self._threshold:
                    return False
        self._keys.add(key)
        if words:
            self._wordsets.append(words)
        return True


def product_development(signals: list[dict], used: "_Used") -> list[str]:
    matches = [s for s in signals if "product" in s.get("topics", []) and s.get("verified")
              and not _REVENUE_SENTENCE.search(s["sentence"])]   # a revenue restatement isn't "development" news
    matches.sort(key=lambda s: -s.get("score", 0))
    out = []
    for s in matches:
        if len(out) >= _MAX_PRODUCT_BULLETS:
            break
        sent = _clean(s["sentence"])
        if used.claim(sent):
            out.append(sent)
    return out


def macro_regulatory(signals: list[dict], used: "_Used") -> list[str]:
    matches = [s for s in signals if "pressure" in s.get("topics", []) and s.get("verified")
              and _MACRO_KEYWORDS.search(s["sentence"]) and not _CUSTOMER_STORY.search(s["sentence"])]
    matches.sort(key=lambda s: -s.get("score", 0))
    candidates = [_clean(s["sentence"]) for s in matches]
    # spread across the real macro themes present (tariff/regulatory/fx/supply/rates) rather than
    # letting whichever theme happens to score highest fill every slot -- one pass per bucket,
    # highest-scored surviving (not-yet-used) sentence in each, before falling back to whatever's left
    by_bucket: dict[str, list[str]] = {}
    for sent in candidates:
        for name, rx in _MACRO_BUCKETS:
            if rx.search(sent):
                by_bucket.setdefault(name, []).append(sent)
                break
    out: list[str] = []
    for name, _ in _MACRO_BUCKETS:
        if len(out) >= _MAX_MACRO_BULLETS:
            break
        for sent in by_bucket.get(name, []):
            if used.claim(sent):
                out.append(sent)
                break
    for sent in candidates:
        if len(out) >= _MAX_MACRO_BULLETS:
            break
        if sent in out:
            continue
        if used.claim(sent):
            out.append(sent)
    return out


def build_narrative(stem: str, segments: list[dict]) -> dict:
    """Returns {} entirely if no cached snapshot exists (never fabricated) -- otherwise each of
    the 4 keys is present only when real evidence was actually found for it."""
    signals = load_signals(stem)
    if not signals:
        return {}
    used = _Used()
    out = {}
    drivers = segment_drivers(signals, segments, used)
    if drivers:
        out["segment_drivers"] = drivers
    comp = competitive_environment(signals, used)
    if comp:
        out["competitive_environment"] = comp
    prod = product_development(signals, used)
    if prod:
        out["product_development"] = prod
    macro = macro_regulatory(signals, used)
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
    used = _Used()
    print("Competitive environment:")
    for s in competitive_environment(signals, used):
        print(" ", s)
    print("\nProduct development:")
    for s in product_development(signals, used):
        print(" ", s)
    print("\nMacro / regulatory:")
    for s in macro_regulatory(signals, used):
        print(" ", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())

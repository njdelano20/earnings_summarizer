"""
summarizer.py
Turns cleaned transcript text into a summary.

Two pieces:
1. extract_metrics() - regex-based pull of numbers that look like revenue, EPS, guidance, etc.
2. extract_key_sentences() - simple keyword-frequency extractive summary (no API needed).

If you later want richer, more natural-language summaries, you can replace
extract_key_sentences() with a call to an LLM API and keep everything else the same.
"""

import re
from collections import Counter
from parser import split_into_sentences

# Keywords that tend to flag financially important sentences
IMPORTANT_KEYWORDS = [
    "revenue", "net income", "earnings per share", "eps", "guidance",
    "margin", "growth", "outlook", "quarter", "year-over-year", "yoy",
    "free cash flow", "operating income", "forecast", "raised", "lowered",
    "beat", "miss", "exceeded", "increase", "decrease",
]


"""
||||||||||||||||||||||||||
||||||||||||||||||||||||||
NEED TO UPDATE METRIC PATTERNS THIS IS THE ORIGINAL BUILD
!!!!!!!!!!!!!!!!!!!!!!!!!
!!!!!!!!!!!!!!!!!!!!!!!!!
"""

METRIC_PATTERNS = {
    "revenue": r"revenue[s]?\s+(?:of|was|were|totaled|reached)?\s*\$?[\d,.]+\s*(?:billion|million|B|M)?",
    "eps": r"(?:earnings per share|EPS)\s+(?:of|was|were)?\s*\$?[\d.]+",
    "net_income": r"net income\s+(?:of|was|were)?\s*\$?[\d,.]+\s*(?:billion|million|B|M)?",
    "guidance": r"guidance\s+(?:of|for|is|was)?[^.]{0,80}",
    "margin": r"(?:gross|operating|net)\s+margin\s+(?:of|was|were)?\s*[\d.]+%?",
}


def extract_metrics(text: str) -> dict:
    """Pull out financial figures using regex patterns. Returns empty list per key if not found."""
    results = {}
    for label, pattern in METRIC_PATTERNS.items():
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        # Dedupe while preserving order, cap at 5 mentions per metric
        seen = []
        for m in matches:
            m_clean = m.strip()
            if m_clean not in seen:
                seen.append(m_clean)
        results[label] = seen[:5]
    return results


def extract_key_sentences(text: str, top_n: int = 8) -> list[str]:
    """
    Very simple extractive summary: score each sentence by how many
    important keywords it contains, then return the top N sentences
    in their original order.
    """
    sentences = split_into_sentences(text)
    scored = []

    for i, sentence in enumerate(sentences):
        lower = sentence.lower()
        score = sum(1 for kw in IMPORTANT_KEYWORDS if kw in lower)
        if score > 0:
            scored.append((i, score, sentence))

    # Sort by score descending, take top_n, then restore original order
    top = sorted(scored, key=lambda x: x[1], reverse=True)[:top_n]
    top_in_order = sorted(top, key=lambda x: x[0])

    return [s for (_, _, s) in top_in_order]


def summarize(text: str) -> dict:
    """Main entry point: returns a dict with metrics + key sentences."""
    return {
        "metrics": extract_metrics(text),
        "key_sentences": extract_key_sentences(text),
    }

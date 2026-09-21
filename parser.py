"""
parser.py
Cleans raw transcript text and splits it into sentences.

All character offsets used elsewhere in the pipeline (evidence spans for facts)
refer to the *cleaned* text returned by clean_text().
"""

import re

_TRANSLATE = str.maketrans({
    " ": " ", " ": " ", " ": " ",
    "​": "", "﻿": "",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "‑": "-", "−": "-",
})


def clean_text(raw_text: str) -> str:
    """Normalize whitespace, quotes and dashes so downstream regexes see one canonical form."""
    text = raw_text.translate(_TRANSLATE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("—", " - ")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# A period after these never ends a sentence.
_NEVER_BOUNDARY = {
    "vs", "inc", "corp", "co", "ltd", "mr", "mrs", "ms", "dr", "st", "no", "nos",
    "approx", "fig", "jr", "sr", "mt", "ft", "est", "avg", "gen", "sen", "rep", "prof",
    "e.g", "i.e", "ph.d",
}
# Dotted abbreviations that can genuinely end a sentence ("...in the U.S. We saw...").
_DOTTED = {"u.s", "u.k", "e.u", "u.s.a", "p.m", "a.m", "d.c"}
# After a dotted abbreviation a capitalised word starts a new sentence ("...in the U.S. Operating income grew...")
# unless it is one of these, which continue a name ("U.S. Treasury", "U.S. News & World Report", "U.S. GAAP").
# The default is to split: a wrong split just breaks one sentence in two, a wrong merge corrupts everything after it.
_DOTTED_CONTINUATIONS = {
    "treasury", "treasuries", "government", "department", "dept", "congress", "senate", "house", "federal", "gaap",
    "dollar", "dollars", "securities", "court", "supreme", "patent", "trade", "customs", "postal", "bank", "bancorp",
    "census", "navy", "army", "air", "steel", "news", "virgin", "open", "bureau", "agency", "administration",
    "attorney", "district", "circuit", "food", "environmental", "china", "chamber", "commerce",
}
_SENTENCE_STARTERS = {
    "We", "The", "In", "Our", "This", "It", "As", "And", "But", "So", "That", "These",
    "I", "For", "With", "At", "On", "If", "Turning", "Looking", "Now", "Let", "Thank",
    "Thanks", "However", "Additionally", "Overall", "Finally", "First", "Second", "Third",
}
_CANDIDATE = re.compile(r"[.!?]+[\"')\]]*(?=\s)")
_PARAGRAPH = re.compile(r"\S[\s\S]*?(?=\n[ \t]*\n|\Z)")


def _is_boundary(text: str, m: re.Match) -> bool:
    core = m.group(0).rstrip("\"')]")
    if core != ".":
        return True
    tok = re.search(r"([A-Za-z][A-Za-z.]*)$", text[:m.start()])
    raw_word = tok.group(1) if tok else ""
    word = raw_word.lower()
    nxt = re.match(r"\s+[\"'(\[]*(\S+)", text[m.end():])
    nxt_word = ""
    if nxt:
        w = re.match(r"[A-Za-z0-9$]+", nxt.group(1))
        nxt_word = w.group(0) if w else ""
    if word in _NEVER_BOUNDARY:
        return False
    if word in _DOTTED:
        return bool(nxt_word) and (nxt_word in _SENTENCE_STARTERS
                                   or (nxt_word[0].isupper() and nxt_word.lower() not in _DOTTED_CONTINUATIONS))
    if len(raw_word) == 1 and raw_word.isupper():
        return False
    is_brand_camel_case = len(nxt_word) > 1 and any(c.isupper() for c in nxt_word[1:])  # iPhone, eBay
    if nxt_word and nxt_word[0].islower() and not is_brand_camel_case:
        return False
    return True


def split_sentences_with_offsets(text: str, base: int = 0) -> list[tuple[int, int, str]]:
    """
    Returns [(start, end, sentence_text)] with offsets into `text` (plus `base`).
    Blank lines always end a sentence; single newlines are treated as spaces.
    """
    out: list[tuple[int, int, str]] = []
    for para in _PARAGRAPH.finditer(text):
        p_start, p_end = para.start(), para.end()
        seg_start = p_start
        for m in _CANDIDATE.finditer(text, p_start, p_end):
            if not _is_boundary(text, m):
                continue
            _emit(text, seg_start, m.end(), base, out)
            seg_start = m.end()
        _emit(text, seg_start, p_end, base, out)
    return out


def _emit(text, start, end, base, out):
    chunk = text[start:end]
    stripped = chunk.strip()
    if not stripped:
        return
    lead = len(chunk) - len(chunk.lstrip())
    s = start + lead
    out.append((base + s, base + s + len(stripped), stripped))


def split_into_sentences(text: str) -> list[str]:
    return [s for _, _, s in split_sentences_with_offsets(text)]

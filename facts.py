"""
facts.py
Deterministic, evidence-linked extraction of financial facts from an earnings call.

A Fact is one typed statement such as
    reported | revenue | services | level | 30.7e9 USD | +12% yoy
    guidance | gross_margin | total | level | 47-48 pct | period "september quarter"
and always carries the exact sentence + character offsets it came from, so every
fact can be checked mechanically against the transcript text (see verify_fact).

Design rules (quality over coverage):
  * Only management speech is mined for facts. Analyst questions, operator and IR
    lines are ignored (an analyst saying "$108 billion" is not a company fact).
  * A figure is only emitted when it can be tied to a metric phrase. Everything
    else is listed in `unclaimed` so gaps are visible instead of silent.
  * Every fact is verified against the source text; suspicious combinations are
    flagged (conflicts, sums that do not add up, out-of-range values).

Output of extract_facts(): {"meta", "facts", "unclaimed", "checks", "stats"}.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict

from transcript import Transcript, Turn, ROLE_MANAGEMENT, ROLE_UNKNOWN

# =========================================================================== #
# Numbers
# =========================================================================== #

_UNIT_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
               "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_ONES = "one|two|three|four|five|six|seven|eight|nine"
_NUMWORD = (
    r"(?:(?:" + "|".join(_TENS_WORDS) + r")(?:[- ](?:" + _ONES + r"))?|"
    + "|".join(sorted(_UNIT_WORDS, key=len, reverse=True))
    + r")(?:\s+and\s+a\s+(?:half|quarter))?|(?:a\s+)?half"
)


def words_to_number(s: str) -> float | None:
    s = s.lower().replace("-", " ").strip()
    if re.fullmatch(r"(?:a\s+)?half", s):
        return 0.5
    m = re.fullmatch(r"(.+?)\s+and\s+a\s+(half|quarter)", s)
    frac = 0.0
    if m:
        s, frac = m.group(1), (0.5 if m.group(2) == "half" else 0.25)
    parts = s.split()
    total = 0
    for p in parts:
        if p in _TENS_WORDS:
            total += _TENS_WORDS[p]
        elif p in _UNIT_WORDS:
            total += _UNIT_WORDS[p]
        else:
            return None
    return total + frac


_N = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_SC = r"(?:trillion|billion|million|thousand|bn|mm|mn|[BMK])(?![A-Za-z])"
_SCALE = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6, "mm": 1e6,
          "mn": 1e6, "thousand": 1e3, "b": 1e9, "m": 1e6, "k": 1e3}


def _num(s: str) -> float:
    s = s.strip()
    if re.match(r"\d", s):
        return float(s.replace(",", ""))
    v = words_to_number(s)
    if v is None:
        raise ValueError(s)
    return v


def _scale(s: str | None) -> float | None:
    return _SCALE[s.lower()] if s else None


_MONEY_BETWEEN = re.compile(
    rf"between\s+(?P<fig>\$\s?(?P<n1>{_N})(?:\s?(?P<s1>{_SC}))?\s+and\s+\$?\s?(?P<n2>{_N})(?:\s?(?P<s2>{_SC}))?)",
    re.I)
_MONEY = re.compile(
    rf"\$\s?(?P<n1>{_N})(?:\s?(?P<s1>{_SC}))?"
    rf"(?:\s*(?:-|to|through)\s*\$?\s?(?P<n2>{_N})(?:\s?(?P<s2>{_SC}))?)?", re.I)
_PCT_BETWEEN = re.compile(
    rf"between\s+(?P<fig>(?P<n1>{_N})\s?%?\s+and\s+(?P<n2>{_N})\s?(?:%|percent\b))", re.I)
_PCT = re.compile(
    rf"(?<![\d.,])(?P<n1>{_N})\s?(?:%|percent\b)(?:\s*(?:-|to|through)\s*(?P<n2>{_N})\s?(?:%|percent\b))?",
    re.I)
_PCT_BARE_RANGE = re.compile(
    rf"(?<![\d.,$])(?P<n1>{_N})\s*(?:-|to|through)\s*(?P<n2>{_N})\s?(?:%|percent\b)", re.I)
_BPS = re.compile(
    rf"(?<![\d.,])(?P<n1>{_N})(?:\s*(?:-|to)\s*(?P<n2>{_N}))?\s+(?:basis\s+points?|bps|bp)\b", re.I)
_PP = re.compile(
    rf"(?<![\d.,A-Za-z])(?P<n1>{_N}|\b(?:{_NUMWORD})\b)(?:\s*(?:-|to)\s*(?P<n2>{_N}|\b(?:{_NUMWORD})\b))?"
    r"\s+percentage\s+points?\b", re.I)

_CHANGE_PRE = re.compile(
    r"\b(?P<verb>up|down|grew|grow|growing|grows|growth\s+of|increased|increasing|increase\s+of|"
    r"decreased|decreasing|decrease\s+of|declined|declining|decline\s+of|higher|lower|"
    r"expand(?:ed|ing)?|contract(?:ed|ing)?)\s+"
    r"(?:(?:by|about|approximately|around|roughly|nearly|almost|over|more\s+than|between)\s+)*"
    r"(?:an?\s+)?(?:(?:strong|solid|impressive|healthy|modest|slight|significant|substantial|robust|sharp)\s+)?$",
    re.I)
_NEGATIVE_VERB = re.compile(r"(?:down|decreas|declin|lower|contract)", re.I)
_BASIS = re.compile(
    r"^\s*,?\s*(?:(?P<yoy>year[- ]over[- ]year|yoy|y/y|from\s+(?:a\s+year\s+ago|the\s+prior[- ]year(?:\s+quarter)?|"
    r"last\s+year|the\s+year[- ]ago\s+(?:quarter|period)|the\s+same\s+quarter\s+last\s+year)|"
    r"versus\s+(?:last\s+year|a\s+year\s+ago|the\s+prior\s+year)|"
    r"over\s+(?:last\s+year|the\s+prior\s+year|a\s+year\s+ago)|"
    r"compared\s+(?:to|with)\s+(?:last\s+year|a\s+year\s+ago|the\s+prior\s+year))|"
    r"(?P<qoq>sequentially|quarter[- ]over[- ]quarter|q/q|qoq|"
    r"from\s+the\s+(?:prior|previous|last)\s+quarter|versus\s+the\s+(?:prior|previous|last)\s+quarter))",
    re.I)
_CONSTANT_CURRENCY = re.compile(
    r"^\s*,?\s*(?:at|in|on)\s+(?:a\s+)?constant[- ]currency|^\s*,?\s*(?:adjusting|adjusted)\s+for\s+currency|^\s*,?\s*at\s+cc\b",
    re.I)
_QUALIFIER = re.compile(
    r"\b(approximately|approx\.?|about|around|roughly|at\s+least|at\s+most|over|more\s+than|greater\s+than|"
    r"higher\s+than|in\s+excess\s+of|under|less\s+than|up\s+to|between|nearly|almost|exceeds?|above|below)\s+(?:an?\s+)?$",
    re.I)

# A figure introduced by from/versus/compared-to and followed by "a year ago" is the prior-period comparator
# ("58% of total revenue, up from 42% a year ago"): neither a growth rate nor a reported value.
_COMPARATOR_PRE = re.compile(
    r"\b(?:from|versus|vs\.?|compared\s+(?:to|with)|relative\s+to|against)\s+(?:(?:approximately|about|around|roughly|nearly)\s+)?$",
    re.I)
_COMPARATOR_POST = re.compile(
    r"^(?:\s+or\s+[\d.$%,\w\s]{1,30}?)?\s*,?\s*(?:a\s+year\s+ago|last\s+year|(?:in\s+)?the\s+(?:prior|year[- ]ago|same))", re.I)


def _is_prior_comparator(text: str, f: "_Fig") -> bool:
    return bool(_COMPARATOR_PRE.search(text[max(0, f.start - 30):f.start])
                and _COMPARATOR_POST.match(text[f.end:f.end + 60]))


@dataclass
class _Fig:
    start: int
    end: int
    raw: str
    kind: str                      # money | pct | bps | pp
    lo: float
    hi: float | None = None
    per_share: bool = False
    is_change: bool = False
    sign: int = 1
    basis: str | None = None
    constant_currency: bool = False
    qualifier: str | None = None
    consumed: bool = False
    chained: bool = False          # a change that repeats the verb of the change before it ("up 6% ... and 7% ...")


def _money_vals(m) -> tuple[float, float | None]:
    n1 = _num(m["n1"])
    n2 = _num(m["n2"]) if m["n2"] else None
    s1, s2 = _scale(m["s1"]), _scale(m["s2"])
    sc1 = s1 or s2 or 1.0
    sc2 = s2 or s1 or 1.0
    return n1 * sc1, (n2 * sc2 if n2 is not None else None)


# "Sequentially, gaming revenue increased 8%": the basis is stated once, at the front of the sentence.
_LEAD_BASIS = re.compile(
    r"^\s*(?:(?:and|but|now|also|then)[, ]+)?(?:(?P<qoq>sequentially|on\s+a\s+sequential\s+basis|quarter[- ]over[- ]quarter)"
    r"|(?P<yoy>year[- ]over[- ]year|on\s+a\s+year[- ]over[- ]year\s+basis))\b", re.I)
_QUAL_WORDS = r"(?:(?:by|about|approximately|around|roughly|nearly|almost|over|more\s+than)\s+)*"
# "up 6% year-over-year AND 7% sequentially": the second figure repeats the verb of the first.
_CHAIN_GAP = re.compile(rf"\s*,?\s*(?:and|,)\s*(?:an?\s+)?{_QUAL_WORDS}", re.I)
# "to more than double" (guidance only): a growth rate stated as a multiple.
_MULTIPLE = re.compile(r"\b(?P<q>more\s+than\s+|at\s+least\s+)?(?P<w>double|triple)\b(?![- ]digit)", re.I)
_PERIOD_LEAD = re.compile(r"\s*(?:in|for|during|over)\s+(?:the\s+)?", re.I)


def _chain_gap_ok(gap: str) -> bool:
    """True when `gap` (text between two figures) is only what joins a chained change: an optional basis, optionally
    one period phrase ('in the second half of 2026'), then 'and' / a comma."""
    bm = _BASIS.match(gap)
    rest = gap[bm.end():] if bm else gap
    if _CHAIN_GAP.fullmatch(rest):
        return True
    for pm in _PERIOD_RE.finditer(rest):
        if _PERIOD_LEAD.fullmatch(rest[:pm.start()]) and _CHAIN_GAP.fullmatch(rest[pm.end():]):
            return True
    return False


def _find_figures(text: str, guidance: bool = False) -> list[_Fig]:
    cands: list[_Fig] = []

    def add(start, end, kind, lo, hi):
        cands.append(_Fig(start, end, text[start:end], kind, lo, hi))

    for m in _MONEY_BETWEEN.finditer(text):
        lo, hi = _money_vals(m)
        add(m.start("fig"), m.end(), "money", lo, hi)
    for m in _MONEY.finditer(text):
        lo, hi = _money_vals(m)
        add(m.start(), m.end(), "money", lo, hi)
    for m in _PCT_BETWEEN.finditer(text):
        add(m.start("fig"), m.end(), "pct", _num(m["n1"]), _num(m["n2"]))
    for m in _PCT.finditer(text):
        add(m.start(), m.end(), "pct", _num(m["n1"]), _num(m["n2"]) if m["n2"] else None)
    for m in _PCT_BARE_RANGE.finditer(text):
        add(m.start(), m.end(), "pct", _num(m["n1"]), _num(m["n2"]))
    for m in _BPS.finditer(text):
        add(m.start(), m.end(), "bps", _num(m["n1"]), _num(m["n2"]) if m["n2"] else None)
    for m in _PP.finditer(text):
        try:
            add(m.start(), m.end(), "pp", _num(m["n1"]), _num(m["n2"]) if m["n2"] else None)
        except ValueError:
            continue
    if guidance:
        for m in _MULTIPLE.finditer(text):
            if re.search(r"\b(?:to|will)\s+$", text[max(0, m.start() - 8):m.start()], re.I):
                add(m.start(), m.end(), "pct", 100.0 if m["w"].lower() == "double" else 200.0, None)

    cands.sort(key=lambda f: (f.start, -(f.end - f.start)))
    figs: list[_Fig] = []
    last_end = -1
    for f in cands:
        if f.start < last_end:
            continue
        figs.append(f)
        last_end = f.end
    for f in figs:
        _classify_fig(f, text)
        mult = _MULTIPLE.fullmatch(f.raw)
        if mult and mult["q"]:
            f.qualifier = re.sub(r"\s+", " ", mult["q"].strip().lower())

    for prev, f in zip(figs, figs[1:]):
        if f.is_change or not prev.is_change or f.kind not in ("pct", "bps", "pp"):
            continue
        if _chain_gap_ok(text[prev.end:f.start]):
            f.is_change, f.sign, f.chained = True, prev.sign, True
    lead = _LEAD_BASIS.match(text)
    if lead:
        for f in figs:
            if f.is_change and f.basis is None and f.start >= lead.end():
                f.basis = "yoy" if lead["yoy"] else "qoq"
    return figs


def _classify_fig(f: _Fig, text: str) -> None:
    pre = text[max(0, f.start - 60):f.start]
    m = _CHANGE_PRE.search(pre)
    if m:
        f.is_change = True
        f.sign = -1 if _NEGATIVE_VERB.match(m["verb"]) else 1
    post = text[f.end:f.end + 90]
    bm = _BASIS.match(post)
    if bm:
        f.basis = "yoy" if bm["yoy"] else "qoq"
    f.constant_currency = bool(_CONSTANT_CURRENCY.match(post))
    if f.kind == "money":
        f.per_share = bool(re.match(r"\s*(?:per\s+(?:diluted\s+)?share|a\s+share)", post, re.I))
    q = _QUALIFIER.search(text[max(0, f.start - 30):f.start])
    if q:
        f.qualifier = re.sub(r"\s+", " ", q.group(1).lower())


# =========================================================================== #
# Metric triggers and segments
# =========================================================================== #

_TRIGGER_DEFS: list[tuple[str, str]] = [
    ("revenue", r"(?:total\s+)?(?:revenues?|net\s+sales)"),
    ("comparable_sales", r"(?:comparable|same[- ]store|comp)\s+sales|comps"),
    ("gross_margin", r"(?:operating\s+)?gross\s+(?:profit\s+)?margins?"),
    ("gross_profit", r"gross\s+profit(?!\s+margin)"),
    ("operating_margin",
     r"(?:adjusted\s+|non-gaap\s+)?operating\s+(?:pretax\s+)?(?:income\s+|profit\s+)?margins?|pretax\s+margins?"),
    ("operating_income",
     r"(?:adjusted\s+|non-gaap\s+)?operating\s+(?:pretax\s+)?(?:income|profit)(?!\s+margin)|pretax\s+income"),
    ("net_income", r"(?:adjusted\s+|non-gaap\s+)?net\s+(?:income|earnings)"),
    ("ebitda_margin", r"(?:adjusted\s+)?ebitda\s+margins?"),
    ("ebitda", r"(?:adjusted\s+)?ebitda(?!\s+margin)"),
    ("eps", r"(?:(?:diluted|basic|adjusted|non-gaap|gaap|operating)\s+)*(?:earnings\s+per\s+(?:diluted\s+)?share|eps)"),
    ("arr", r"arr|annual(?:ized)?\s+recurring\s+revenue"),
    ("backlog", r"backlog"),
    ("bookings", r"bookings"),
    ("signings", r"signings"),
    ("free_cash_flow", r"free\s+cash\s+flow"),
    ("operating_cash_flow",
     r"operating\s+cash\s+flows?|cash\s+flows?\s+from\s+operat\w+|cash\s+from\s+(?:continuing\s+)?operations"),
    ("capex", r"capital\s+expenditures?|capex"),
    ("operating_expenses", r"(?:total\s+)?operating\s+expenses?|opex"),
    ("cash_and_securities",
     r"cash(?:,\s*|\s+)(?:cash\s+equivalents,?\s+)?and\s+(?:marketable\s+securities|short-term\s+investments|cash\s+equivalents|investments)"),
    ("cash", r"cash(?=\s+of\b)|cash\s+balance"),
    ("total_debt", r"(?:total|net|gross)\s+debt|debt\s+balance|debt(?=\s+(?:of|was|at)\b)"),
    ("share_repurchases", r"share\s+repurchases?|share\s+buybacks?|buybacks?|repurchases?"),
    ("dividends", r"dividends?"),
    ("capital_returned",
     r"(?:returned|returning)(?=\s+(?:over\s+|about\s+|approximately\s+|nearly\s+|more\s+than\s+)?\$)"),
    ("tax_rate", r"(?:effective\s+|adjusted\s+|non-gaap\s+)?(?:income\s+)?tax\s+rate"),
    ("oie", r"oi&e|oie|other\s+income\s+(?:and|&)\s+expense|other\s+income\s*/\s*expense"),
]
_GROWTH_RATE_TRIGGER = r"(?:(?:reported|organic|constant[- ]currency|year[- ]over[- ]year|yoy|sequential)\s+)*growth\s+rate"

_TRIGGER_RES = [(name, re.compile(rf"\b(?:{pat})\b", re.I)) for name, pat in _TRIGGER_DEFS]
_GROWTH_RATE_RE = re.compile(rf"\b{_GROWTH_RATE_TRIGGER}\b", re.I)

SEGMENTED_METRICS = {"revenue", "comparable_sales", "gross_margin", "gross_profit",
                     "operating_margin", "operating_income", "ebitda", "ebitda_margin",
                     "arr", "backlog", "bookings", "signings"}
PCT_LEVEL_METRICS = {"gross_margin", "operating_margin", "ebitda_margin", "tax_rate"}

_STOP = set("""
was were is are be been being our the a an in of for to with that which as by from at on both all every
record quarter quarters new strong we i this these those its their also but or while despite driven up down
grew saw set reached delivered posted reported achieved including across during each first second third
fourth last prior year fiscal solid healthy robust impressive modest significant incredible all-time best
better very adjusted non-gaap gaap organic january february march april may june july august september
october november december growth double-digit single-digit digit digits had has have having generated came
coming totaled totalled operating expanded expand expanding improved improving improve increased decreased
declined reduced accelerated raised lowered year-over-year yoy sequential sequentially basis points point
percentage bps bp expect expects expected expecting anticipate anticipates anticipated project projects
projected forecast forecasts guide guides guiding estimate estimates believe think assume see seeing continue
continues remain remains remained deliver drive driving provide provided provides report reporting include
includes included reflect reflects reflecting exclude excludes excluding impacted impact would will should
could may might can do does did not no than more less about around approximately roughly over under above
below between near nearly almost still now currently then therefore however thus so if when where what how
why who there here into through per versus vs it's that's we're you your my he she they them us
""".split())
_BAD_SEGMENT = re.compile(
    r"\b(?:my|your|you|we|i|question|follow[- ]?up|call|quarter|year|period|month|first|second|third|fourth|full|"
    r"our|metrics?|results?|performance|numbers?|profit|side|details?|picture|perspective|overall|"
    r"addition|summary|closing|particular|general|conclusion|contrast|fact|short)\b",
    re.I)
# Words that name no business on their own ("Segment revenue grew 6%"): the segment must come from context instead.
_GENERIC_SEGMENT_WORDS = {"segment", "segments", "business", "businesses", "division", "addition"}
_DROP_SEGMENT_WORDS = {"total", "company", "consolidated", "overall", "worldwide", "global",
                       "our", "the", "companys"}
_DROP_TRAILING_SEGMENT_WORDS = {"business", "segment"}
_SCALE_WORDS = set(_SCALE) | {"dollars", "dollar"}
_TOKEN = re.compile(r"(?:[A-Za-z]\.){2,}|[A-Za-z][A-Za-z0-9+&'\-]*|\d[\d.,]*|,|&")   # "U.S." is one token, not "U" and "S"


def _is_connector(tok: str) -> bool:
    return tok in (",", "&") or tok.lower() == "and"


def _is_name(tok: str) -> bool:
    low = tok.lower()
    return (bool(re.match(r"[A-Za-z]", tok)) and not _is_connector(tok)
            and low not in _STOP and low not in _SCALE_WORDS)


def _segment_words_before(pre: str) -> list[str]:
    toks = _TOKEN.findall(pre)
    i = len(toks) - 1
    seg: list[str] = []
    while i >= 0 and len(seg) < 8:
        t = toks[i]
        if _is_connector(t):
            k = i
            while k >= 0 and _is_connector(toks[k]):
                k -= 1
            if k >= 0 and _is_name(toks[k]):
                seg.extend(toks[i:k:-1])
                i = k
                continue
            break
        if _is_name(t):
            seg.append(t)
            i -= 1
            continue
        break
    seg.reverse()
    while seg and _is_connector(seg[-1]):
        seg.pop()
    return seg


def _normalize_segment(words: list[str]) -> str:
    kept = [w for w in words if w.lower() not in _DROP_SEGMENT_WORDS]
    while kept and kept[-1].lower() in _DROP_TRAILING_SEGMENT_WORDS and len(kept) > 1:
        kept.pop()
    while kept and _is_connector(kept[0]):
        kept.pop(0)
    while kept and _is_connector(kept[-1]):
        kept.pop()
    if not kept:
        return "total"
    s = " ".join(kept).lower()
    return re.sub(r"\s+,", ",", s)


_SEG_AFTER = re.compile(
    r"^\s+(?:for|from)\s+(?:the\s+)?(?P<seg>[A-Za-z][A-Za-z0-9+&'\- ,]{1,60}?)\s+"
    r"(?:was|were|came|is|are|of|reached|totaled|grew|declined|to|will)\b", re.I)
_SEG_LEADING = re.compile(
    r"^\s*(?:In|For|On|Within|As\s+for|Turning\s+(?:now\s+)?to|Moving\s+(?:now\s+)?to|Looking\s+at|"
    r"Starting\s+with|Let's\s+(?:turn|move)\s+to|Now\s+(?:turning|moving)\s+to)\s+(?:the\s+)?"
    r"(?P<seg>[^,.;:]{1,50}),", re.I)
_SEG_GROWTH_FOR = re.compile(
    r"^\s+for\s+(?:our\s+|the\s+)?(?P<seg>[A-Za-z][A-Za-z0-9+&'\- ,]{1,50}?)\s+(?:to|will|is|should|was)\b", re.I)


_HEAD_LEAD = re.compile(
    r"^\s*(?:(?:now|next|and|so)[, ]+)?(?:turning|moving|starting|looking|switching|let(?:'s|\s+us)\s+(?:turn|move))"
    r"\s+(?:now\s+)?(?:to|with|at)\s+", re.I)
_HEAD_STARTING_WITH = re.compile(r"\bstarting\s+with\s+(?:the\s+|our\s+)?(?P<seg>[^,.;]+)", re.I)
_HEAD_NOT_SEGMENT = re.compile(
    r"\b(?:segments|balance|sheet|cash|flow|outlook|guidance|results|quarter|financial|details?|questions?|overview|"
    r"summary|half|year|stack|market)\b", re.I)


def _section_from_heading(text: str) -> tuple[bool, str | None]:
    """A heading sentence ("Turning to our Embedded segment.", "..., starting with the data center segment.").
    Returns (is_heading, segment): a heading that names no single business ("Turning to the balance sheet") returns
    (True, None) so the previous section is closed. Only business-like names count: an explicit "segment"/"business"
    word, or every word capitalized ("Client and Gaming")."""
    m = _HEAD_LEAD.match(text)
    if not m:
        return False, None
    rest = text[m.end():]
    sw = _HEAD_STARTING_WITH.search(rest)
    if sw:
        rest = sw["seg"]
    rest = re.split(r"[.,;:]", rest, maxsplit=1)[0].strip()
    rest = re.sub(r"^(?:(?:the|our)\s+)+", "", rest, flags=re.I)
    words = [w for w in _TOKEN.findall(rest) if not _is_connector(w)]
    if not words or _HEAD_NOT_SEGMENT.search(rest):
        return True, None
    explicit = words[-1].lower() in _DROP_TRAILING_SEGMENT_WORDS
    proper = all(w[0].isupper() for w in words)
    if not (explicit or proper):
        return True, None
    seg = _normalize_segment(_TOKEN.findall(rest))
    return True, (None if seg == "total" or seg in _GENERIC_SEGMENT_WORDS else seg)


@dataclass
class _Trig:
    metric: str
    start: int
    end: int
    text: str
    segment: str | None
    growth_rate: bool = False
    used: bool = False
    seg_lower: bool = False        # segment phrase has no capitalized word (e.g. "our services revenue")
    seg_from_subject: bool = False  # segment taken from the sentence subject, not from words next to the metric
    seg_from_section: bool = False  # segment carried over from a heading sentence ("Turning to our Embedded segment.")


def _find_triggers(text: str, section_seg: str | None = None) -> list[_Trig]:
    raw: list[tuple[int, int, str, bool]] = []
    for name, rx in _TRIGGER_RES:
        for m in rx.finditer(text):
            raw.append((m.start(), m.end(), name, False))
    for m in _GROWTH_RATE_RE.finditer(text):
        raw.append((m.start(), m.end(), "revenue", True))
    raw.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    kept: list[tuple[int, int, str, bool]] = []
    last_end = -1
    for r in raw:
        if r[0] < last_end:
            continue
        kept.append(r)
        last_end = r[1]

    trigs: list[_Trig] = []
    for start, end, metric, growth in kept:
        seg = None
        from_subject = False
        from_section = False
        if metric in SEGMENTED_METRICS:
            pre = text[:start]
            # A comma right before the metric ends an introduction ("Later, revenue ...");
            # real "In iPad, revenue ..." introductions are handled by _SEG_LEADING below.
            words = [] if pre.rstrip().endswith(",") else _segment_words_before(pre)
            if words and all(w.lower() in _GENERIC_SEGMENT_WORDS for w in words if not _is_connector(w)):
                words = []                # "Segment revenue grew 6%": names no business, so look at the context
            if not words:
                after = _SEG_GROWTH_FOR.match(text[end:]) if growth else _SEG_AFTER.match(text[end:])
                if after and not _BAD_SEGMENT.search(after["seg"]):
                    words = _TOKEN.findall(after["seg"])
                else:
                    lead = _SEG_LEADING.match(text)
                    if lead and start > lead.end() and not _BAD_SEGMENT.search(lead["seg"]):
                        words = _TOKEN.findall(lead["seg"])
                    elif start > 0:
                        subj = _leading_subject(text, start)
                        if subj:
                            words, from_subject = subj, True
                if not words and section_seg:
                    words, from_section = _TOKEN.findall(section_seg), True
            seg = _normalize_segment(words)
            seg_lower = seg != "total" and not any(
                any(c.isupper() for c in w) for w in words if not _is_connector(w)
                and w.lower() not in _DROP_SEGMENT_WORDS)
        else:
            seg, seg_lower = "total", False
        trigs.append(_Trig(metric, start, end, text[start:end], seg, growth,
                           seg_lower=seg_lower and not from_section, seg_from_subject=from_subject,
                           seg_from_section=from_section))
    return trigs


def _accounting_basis(text: str, t: _Trig) -> str | None:
    if t.metric == "eps" and re.search(r"\boperating\b", t.text, re.I):
        return "non_gaap"
    window = text[max(0, t.start - 30):t.end].lower()
    if "non-gaap" in window or "adjusted" in window:
        return "non_gaap"
    if re.search(r"\bgaap\b", window):
        return "gaap"
    return None


# =========================================================================== #
# Sentence-level cues
# =========================================================================== #

_GUIDE_CUE = re.compile(
    r"\b(?:we|the\s+company|management)\s+(?:currently\s+|now\s+|also\s+|still\s+|do\s+|would\s+|continue\s+to\s+|are\s+)*"
    r"(?:expect(?:s|ing)?|anticipat(?:e|es|ing)|project(?:s|ing)?|forecast(?:s|ing)?|guid(?:e|es|ing))\b"
    r"|\bexpected\s+(?:to|benefit|impact|headwind|tailwind)\b|\b(?:is|are)\s+expected\b"
    r"|\bour\s+(?:outlook|guidance)\b|\b(?:full[- ]year|year)\s+(?:outlook|guidance)\b|\bon\s+track\s+to\b", re.I)

_PERIOD_RE = re.compile(
    r"\b(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+quarter"
    r"|(?:first|second|third|fourth)\s+quarter(?:\s+of\s+(?:fiscal\s+)?(?:year\s+)?20\d\d)?"
    r"|Q[1-4](?:\s*(?:FY\s*)?'?(?:20)?\d{2})?"
    r"|full[- ]year(?:\s+(?:fiscal\s+)?20\d\d)?|fiscal\s+(?:year\s+)?20\d\d"
    r"|(?:first|second)\s+half(?:\s+of\s+(?:the\s+year|(?:fiscal\s+)?20\d\d))?"
    r"|for\s+the\s+(?:full\s+)?year(?:\s+20\d\d)?|this\s+year|next\s+year|year[- ]to[- ]date|(?:in|for|during)\s+20\d\d"
    r"|(?:over|in)\s+the\s+(?:last|past)\s+(?:12|twelve)\s+months|trailing\s+(?:12|twelve)[- ]months?)\b", re.I)

_DESCRIPTOR = re.compile(
    r"\b(?:(?:low|mid|high|upper|lower)[- ]?(?:single[- ]digits?|double[- ]digits?|teens|twenties)"
    r"|double[- ]digits?|single[- ]digits?|(?:roughly|approximately)\s+flat|flat"
    r"|(?:largely|roughly|broadly)\s+(?:similar|consistent|in\s+line|flat)\b[^.;]{0,140})", re.I)

_DRIVERS = [
    (re.compile(r"tariff\s+refunds?", re.I), "tariff refunds"),
    (re.compile(r"tariffs?", re.I), "tariffs"),
    (re.compile(r"foreign\s+(?:exchange|currency)|\bFX\b|currency", re.I), "foreign exchange"),
    (re.compile(r"supply\s+constraints?", re.I), "supply constraints"),
    (re.compile(r"acquisitions?", re.I), "acquisitions"),
    (re.compile(r"divestitures?", re.I), "divestitures"),
    (re.compile(r"extra\s+week|53rd\s+week", re.I), "extra week"),
    (re.compile(r"restructuring", re.I), "restructuring"),
]
_ADJUSTED_AFTER = re.compile(r"\b(?:adjusted\s+for|excluding|without|removing|ex-)\b", re.I)
_FAVORABLE = re.compile(r"\b(?:favorable|benefit|tailwind|positive|contribut\w*)\b", re.I)
_UNFAVORABLE = re.compile(r"\b(?:unfavorable|headwinds?|drag|negative|dilutive)\b", re.I)
_IMPACT_BEFORE = re.compile(
    r"(?:impact|benefit|headwinds?|tailwinds?|drag|contribution)\s+(?:of|to|from|was|is|at)?\s*(?:an?\s+)?"
    r"(?:(?:about|approximately|over|around|roughly|nearly|more\s+than)\s+)*$", re.I)
_IMPACT_AFTER = re.compile(
    r"^\s*(?:of\s+)?(?:(?:favorable|unfavorable|positive|negative)\s+)?(?:impact|benefit|headwinds?|tailwinds?|drag)\b",
    re.I)


def _is_impact_figure(text: str, f: "_Fig") -> bool:
    """A figure sized as an impact ('headwind of about 2 points', '$0.11 of favorable impact')."""
    return bool(_IMPACT_BEFORE.search(text[max(0, f.start - 50):f.start])
                or _IMPACT_AFTER.match(text[f.end:f.end + 40]))


def _norm_period(s: str) -> str:
    p = re.sub(r"\s+", " ", s.strip().lower())
    if re.search(r"(?:last|past)\s+(?:12|twelve)\s+months|trailing", p):
        return "trailing 12 months"
    if re.fullmatch(r"for the (?:full )?year|this year|full[- ]year", p):
        return "full year"
    if p == "year-to-date" or p == "year to date":
        return "year to date"
    year = re.fullmatch(r"(?:in|for|during) (20\d\d)", p)
    if year:
        return year.group(1)
    year = re.fullmatch(r"(?:for the )?(?:full )?year (20\d\d)|full[- ]year (20\d\d)", p)
    if year:
        return year.group(1) or year.group(2)
    if re.match(r"(?:first|second) half", p):
        return re.sub(r"\s+of\s+the\s+year$", "", p)
    return p


def _periods_in(text: str) -> list[str]:
    return [_norm_period(m.group(0)) for m in _PERIOD_RE.finditer(text)]


def _nearest_period(text: str, pos: int, max_distance: int = 90) -> str | None:
    """Period phrase closest to `pos` in the sentence (fixes 'Mar quarter ... June quarter' comparisons)."""
    best, best_d = None, max_distance + 1
    for m in _PERIOD_RE.finditer(text):
        d = 0 if m.start() <= pos < m.end() else min(abs(m.start() - pos), abs(m.end() - pos))
        if d < best_d:
            best, best_d = _norm_period(m.group(0)), d
    return best if best_d <= max_distance else None


def _period_after(text: str, pos: int, limit: int | None = None, max_distance: int = 70) -> str | None:
    """First period phrase at or after `pos` and before `limit`
    ('... 80% year-over-year in the second half of 2026 and more than 70% for the full year 2027')."""
    for m in _PERIOD_RE.finditer(text):
        if m.start() >= pos:
            if m.start() - pos > max_distance or (limit is not None and m.start() >= limit):
                return None
            return _norm_period(m.group(0))
    return None


# A guidance period carries over to the next sentences only while the outlook passage continues.
_GUIDANCE_CTX_TTL = 4                     # sentences
_SECTION_TTL = 2                          # sentences a segment heading keeps applying to metric-first sentences


# =========================================================================== #
# Fact model
# =========================================================================== #

@dataclass
class Fact:
    kind: str                       # reported | guidance | declared
    metric: str
    segment: str | None
    stat: str                       # level | growth | change | impact
    value: float | None
    value_high: float | None
    unit: str                       # USD | USD_per_share | pct | bps | pp
    qualifier: str | None = None
    basis: str | None = None        # yoy | qoq (for growth stats)
    accounting: str | None = None   # gaap | non_gaap | adjusted
    currency_basis: str | None = None  # constant (constant-currency growth) | None (as reported)
    descriptor: str | None = None   # qualitative guidance ("mid-teens", "largely similar ...")
    driver: str | None = None       # for impact facts ("tariff refunds")
    direction: str | None = None    # favorable | unfavorable (impact)
    context_metric: str | None = None
    context_segment: str | None = None
    changes: list[dict] = field(default_factory=list)
    period: str | None = None
    speaker: str = ""
    role: str = ""
    section: str = ""
    turn_index: int = 0
    sentence_index: int = 0
    sentence: str = ""
    ev_start: int = 0
    ev_end: int = 0
    figure_text: str | None = None
    confidence: str = "high"
    flags: list[str] = field(default_factory=list)
    mentions: int = 1
    verified: bool = False
    id: str = ""


_CONF_RANK = {"high": 3, "medium": 2, "low": 1}
_RANK_CONF = {v: k for k, v in _CONF_RANK.items()}


def _cap_conf(current: str, cap: str) -> str:
    return _RANK_CONF[min(_CONF_RANK[current], _CONF_RANK[cap])]


# =========================================================================== #
# Sentence extraction
# =========================================================================== #

def _window_ok(text: str, a: int, b: int) -> bool:
    gap = text[a:b]
    return ";" not in gap and not re.search(r"\bbut\b", gap, re.I)


_ENTITY_OK = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
              "october", "november", "december", "gaap", "non-gaap", "i", "we", "our", "the", "it",
              "this", "that", "q1", "q2", "q3", "q4", "fy"}


def _gap_has_entity(gap: str) -> bool:
    """A capitalized name inside the metric->figure gap ('growth in Hybrid Infrastructure of 6%')
    means the figure probably belongs to that entity, not to the metric's own subject."""
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9+&'\-.]*", gap):
        if any(c.isupper() for c in tok) and tok.lower().rstrip(".") not in _ENTITY_OK \
                and not re.fullmatch(r"(?:Q[1-4]|FY\d*)", tok):
            return True
    return False


def _left_trigger(f: _Fig, trigs: list[_Trig], others: list[_Fig], text: str) -> _Trig | None:
    best = None
    for t in trigs:
        if t.used or t.end > f.start or f.start - t.end > 110:
            continue
        if any(t.end <= o.start and o.end <= f.start for o in others if o is not f):
            continue
        if not _window_ok(text, t.end, f.start):
            continue
        if _gap_has_entity(text[t.end:f.start]):
            continue
        if best is None or t.end > best.end:
            best = t
    return best


_GROWTH_WORD_AFTER_FIG = r"(?:(?:growth|increase|decrease|decline|improvement)\s+)?"


def _right_trigger(f: _Fig, trigs: list[_Trig], text: str) -> tuple[_Trig | None, bool]:
    """Trigger phrase right after a figure ('$4B in dividends', '7% growth in our adjusted EBITDA').
    Returns (trigger, growth_cue)."""
    after = text[f.end:]
    m = re.match(rf"\s*({_GROWTH_WORD_AFTER_FIG})(?:(?:in|of|for|from|on)\s+)?", after, re.I)
    base = f.end + m.end()
    fillers = re.match(r"(?:(?:the|our|a|an|total|net|consolidated|overall|annual|full[- ]year|long[- ]term)\s+)*",
                       text[base:], re.I)
    limit = base + fillers.end()
    for t in trigs:
        if not t.used and base <= t.start <= limit:
            growth = bool(m.group(1).strip()) or bool(
                re.match(r"\s*(?:growth|increase|decline|decrease)\b", text[t.end:], re.I))
            return t, growth
    return None, False


_SUBJECT_NOT_SEGMENT = {
    "we", "i", "our", "the", "this", "it", "that", "these", "those", "there", "here", "today", "overall",
    "also", "and", "so", "now", "as", "for", "in", "on", "at", "with", "from", "during", "after", "before",
    "while", "when", "if", "since", "given", "based", "because", "first", "second", "third", "finally",
    "looking", "turning", "moving", "let", "thanks", "thank", "importantly", "additionally", "however",
    "what", "how", "why", "total", "company", "revenue", "operating", "net", "gross", "diluted", "free",
    "adjusted", "earnings", "cash", "income", "gaap", "non-gaap",
    "later", "then", "next", "last", "earlier", "meanwhile", "similarly", "likewise", "moreover",
    "consequently", "sequentially", "separately", "specifically", "historically", "typically",
    "generally", "clearly", "obviously", "again", "still", "yes", "no", "well", "okay", "sure",
    "segment", "segments", "business", "addition",
}
_LEADING_SUBJECT = re.compile(
    r"^\s*(?:(?:Today|Overall|Now|Also|And|So),?\s+)?"
    r"(?P<subj>[A-Z][A-Za-z0-9+&'\-]*(?:\s+[A-Z][A-Za-z0-9+&'\-]*){0,2})\b")


_SUBJECT_FOLLOWER = re.compile(
    r"\s*(?:,|(?:was|were|is|are|had|has|delivered|grew|grow|grows|declined|reported|generated|posted|achieved|"
    r"increased|decreased|came|saw|contributed|expanded|accelerated|continued|remained|did|also|again|just|now|"
    r"showed|produced|drove|ended|revenue|revenues)\b)", re.I)


def _leading_subject(text: str, before: int) -> list[str] | None:
    """Capitalized subject at the sentence start (e.g. 'Transaction Processing'), if it ends before `before`."""
    m = _LEADING_SUBJECT.match(text)
    if not m or m.end() > before - 2:
        return None
    first = m["subj"].split()[0].lower()
    single = len(m["subj"].split()) == 1
    if first in _SUBJECT_NOT_SEGMENT or first in _STOP or (single and first.endswith("ing")):
        return None
    if not _SUBJECT_FOLLOWER.match(text[m.end():]):
        return None                      # "Solid demand for ..." : the capitalized word is part of a longer noun phrase
    if single and text[m.end():].lstrip().startswith(","):
        return None                      # "Historically, revenue ..." is an adverbial opener, not a segment
    return _TOKEN.findall(m["subj"])


_WE_CUE = re.compile(
    r"\bwe\s+(?:also\s+|just\s+|again\s+)?(?:delivered|generated|reported|posted|achieved|recorded|had|produced|"
    r"drove|saw|grew)\s+(?:a\s+)?(?:record\s+)?(?:of\s+)?$", re.I)


def _subject_segment(text: str, f: _Fig) -> tuple[str | None, str]:
    """Segment for a 'FIGURE in revenue' phrase, from the sentence subject. Returns (segment, source)."""
    pre = text[:f.start]
    if re.search(r"\b(?:pleased|proud|happy|excited)\s+to\s+report(?:ed)?\s*(?:a\s+)?(?:record\s+)?$", pre, re.I) \
            or re.search(r"\breport(?:ed|ing)?\s+(?:a\s+)?(?:record\s+)?$", pre, re.I) \
            or _WE_CUE.search(pre):
        return "total", "report_cue"
    words = _leading_subject(text, f.start)
    if words:
        return _normalize_segment(words), "subject"
    return None, "unresolved"


_GROWTH_GAP = re.compile(r"\b(?:grow\w*|increas\w*|declin\w*|decreas\w*|up|down|expand\w*|higher|lower)\b", re.I)


@dataclass
class _Rec:
    metric: str
    segment: str | None
    stat: str
    unit: str
    lo: float | None
    hi: float | None
    fig: _Fig | None
    trig: _Trig | None
    how: str = "left"
    qualifier: str | None = None
    basis: str | None = None
    accounting: str | None = None
    descriptor: str | None = None
    driver: str | None = None
    direction: str | None = None
    changes: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    growth_inferred: bool = False
    segment_source: str = "trigger"
    currency_basis: str | None = None
    context_from: str | None = None       # "sentence" | "previous" | None
    context_metric: str | None = None
    context_segment: str | None = None
    figure_text: str | None = None
    order: int = 0
    reference: bool = False               # the figure is a target being exceeded/undershot, so the fact is forward-looking
    after_ok: bool = False                # a level/growth figure whose own period may follow it in the sentence
    after_limit: int | None = None        # ...but only before this offset (start of the next figure)


def _money_unit(f: _Fig, metric: str) -> str:
    return "USD_per_share" if (f.per_share or metric == "eps") else "USD"


def _change_dict(c: _Fig) -> dict:
    return {"value": c.sign * c.lo, "value_high": (c.sign * c.hi if c.hi is not None else None),
            "unit": {"money": "USD", "pct": "pct", "bps": "bps", "pp": "pp"}[c.kind],
            "basis": c.basis, "constant_currency": c.constant_currency, "text": c.raw}


_TO_LEVEL = re.compile(r"\s*(?:to|at|reaching)\s+(?:a\s+)?(?:record\s+)?(?:new\s+)?(?:(?:approximately|about|nearly|over)\s+)?", re.I)


def _lead_changes(f: _Fig, figs: list[_Fig], text: str) -> list[_Fig]:
    """Changes stated BEFORE their level: 'revenue increased 50% year-over-year and 13% sequentially to a record
    $11.5 billion'. Returns the run of change figures that lead into `f` (empty if `f` is not introduced this way)."""
    if f.kind != "money":
        return []
    out: list[_Fig] = []
    nxt = f.start
    j = next(i for i, g in enumerate(figs) if g is f) - 1
    while j >= 0 and figs[j].is_change and figs[j].kind in ("pct", "bps", "pp"):
        gap = text[figs[j].end:nxt]
        bm = _BASIS.match(gap)
        rest = gap[bm.end():] if bm else gap
        if not (_chain_gap_ok(gap) if out else _TO_LEVEL.fullmatch(rest)):
            break
        out.append(figs[j])
        nxt = figs[j].start
        j -= 1
    out.reverse()
    return out


# "grow substantially above our prior target of greater than 35%": the figure is a reference point, not the guidance.
_REL_DESC = re.compile(
    r"\b(?:substantially|significantly|materially|meaningfully)\s+(?:above|below|ahead\s+of|exceed(?:ing)?|higher\s+than|lower\s+than)\b",
    re.I)
# "very strong double-digit growth in our data center segment", "significant double-digit decline in gaming"
_SEG_DESCRIPTOR = re.compile(
    r"\b(?P<desc>(?:(?:very|strong|significant|modest|slight|solid|substantial)\s+)*(?:(?:low|mid|high)[- ]?)?"
    r"(?:double|single)[- ]digit\s+(?:growth|decline|decrease|increase))\s+in\s+(?:both\s+)?(?:our\s+|the\s+)?"
    r"(?P<segs>[A-Za-z][A-Za-z0-9+&' ,-]{1,60}?)"
    r"(?=\s+(?:segments?|business(?:es)?)\b|\s*[.;]|\s+(?:with|offset)\b|,)", re.I)
# An elliptical outlook line right after a "we expect ..." sentence: "Non-GAAP operating expenses to be approximately
# $3.65 billion" or "Strong double-digit growth in our embedded segment ...".
_GUIDE_CONTINUATION = re.compile(
    r"^\s*(?:and\s+)?(?:"
    r"(?:(?:very|strong|significant|modest|slight|solid|substantial)\s+)*(?:double|single)[- ]digit\s+"
    r"(?:growth|decline|decrease|increase)\b"
    r"|(?:(?:non-gaap|gaap)\s+)?[A-Za-z][A-Za-z&' /-]{0,60}?\s+to\s+be\s+(?:approximately|about|around|roughly|at)\b)", re.I)


def _rel_descriptor(text: str, f: _Fig, trig: _Trig | None) -> str | None:
    lo = max(trig.end if trig else 0, f.start - 90)
    found = list(_REL_DESC.finditer(text[lo:f.start]))
    return re.sub(r"\s+", " ", found[-1].group(0).lower()) if found else None


def _lead_basis(text: str) -> str | None:
    m = _LEAD_BASIS.match(text)
    return None if not m else ("yoy" if m["yoy"] else "qoq")


def _process_sentence(text: str, is_guidance: bool, section_seg: str | None = None) -> tuple[list[_Rec], list[dict]]:
    figs = _find_figures(text, guidance=is_guidance)
    trigs = _find_triggers(text, section_seg)
    driver = next((label for rx, label in _DRIVERS if rx.search(text)), None)
    impact_figs = [f for f in figs if driver and _is_impact_figure(text, f)]
    mains = [f for f in figs if not f.is_change and f not in impact_figs]
    changes = [f for f in figs if f.is_change and f not in impact_figs]
    recs: list[_Rec] = []
    by_fig: dict[int, _Rec] = {}
    unclaimed_reasons: dict[int, str] = {}
    preattached: set[int] = set()          # change figures already attached to the level that follows them

    others = [g for g in figs if g not in impact_figs]

    for f in mains:
        if _is_prior_comparator(text, f):
            unclaimed_reasons[id(f)] = "prior_period_comparator"
            continue
        t, growth_cue = _right_trigger(f, trigs, text)
        how = "right"
        pre_ch = _lead_changes(f, figs, text)
        if t is None:
            # The lead-in changes sit between the metric and the level ("revenue increased 50% ... to $11.5 billion")
            # and would otherwise block the metric from reaching it.
            t = _left_trigger(f, trigs, [o for o in others if not any(o is c for c in pre_ch)], text)
            how = "left"
            growth_cue = False
        if t is None:
            continue

        unit = {"money": _money_unit(f, t.metric), "pct": "pct", "bps": "bps", "pp": "pp"}[f.kind]
        stat = "level"
        basis = None
        if f.kind == "pct" and t.metric not in PCT_LEVEL_METRICS:
            gap = text[t.end:f.start] if how == "left" else ""
            if growth_cue or (how == "left" and (_GROWTH_GAP.search(gap) or f.basis)):
                stat, basis = "growth", f.basis
            else:
                unclaimed_reasons[id(f)] = "pct_without_growth_cue"
                continue
        elif f.kind in ("bps", "pp"):
            stat = "change"

        segment, seg_source = t.segment, "trigger"
        if t.seg_from_subject:
            seg_source = "subject"
        elif t.seg_from_section:
            seg_source = "section"
        if how == "right" and t.metric == "revenue" and (t.segment == "total" or t.seg_from_subject) \
                and not _segment_words_before(text[:t.start]):
            segment, seg_source = _subject_segment(text, f)
        rec = _Rec(metric=t.metric, segment=segment, stat=stat, unit=unit, lo=f.lo, hi=f.hi,
                   fig=f, trig=t, how=how, qualifier=f.qualifier, basis=basis,
                   accounting=_accounting_basis(text, t), growth_inferred=t.growth_rate,
                   segment_source=seg_source, figure_text=f.raw, order=f.start, after_ok=True,
                   after_limit=next((g.start for g in figs if g.start >= f.end), None))
        if segment is None:
            rec.flags.append("segment_unresolved")
        elif seg_source == "subject":
            rec.flags.append("segment_from_subject")
        elif seg_source == "section":
            rec.flags.append("segment_from_section")
        elif t.seg_lower:
            rec.flags.append("segment_lowercase_phrase")
        if stat == "growth" and basis is None:
            rec.flags.append("basis_unspecified")
        if stat == "level" and rec.accounting is None and _ADJUSTED_AFTER.search(text[f.end:f.end + 60]):
            rec.accounting = "adjusted"
            rec.flags.append("adjusted_basis")
        if t.growth_rate:
            rec.flags.append("metric_inferred_from_growth_rate")
        rel =_rel_descriptor(text, f, t if how == "left" else None) if (is_guidance or re.search(r"\bwill\b", text)) else None
        if rel:
            rec.descriptor, rec.reference = rel, True
            rec.flags.append("figure_is_reference_target")
        t.used = True
        f.consumed = True
        recs.append(rec)
        by_fig[id(f)] = rec
        for c in pre_ch:
            rec.changes.append(_change_dict(c))
            preattached.add(id(c))
            c.consumed = True
            if c.kind != "pct":
                continue
            grec = _Rec(metric=t.metric, segment=segment, stat="growth", unit="pct", lo=c.sign * c.lo,
                        hi=(c.sign * c.hi if c.hi is not None else None), fig=c, trig=t, how="left",
                        qualifier=c.qualifier, basis=c.basis, accounting=rec.accounting,
                        growth_inferred=t.growth_rate, segment_source=seg_source, figure_text=c.raw, order=c.start,
                        currency_basis="constant" if c.constant_currency else None,
                        flags=[fl for fl in rec.flags if fl.startswith("segment_")])
            if c.basis is None:
                grec.flags.append("basis_unspecified")
            recs.append(grec)

    for c in changes:
        if id(c) in preattached:
            continue
        if _is_prior_comparator(text, c):
            unclaimed_reasons[id(c)] = "prior_period_comparator"
            continue
        change = _change_dict(c)
        cands = [m for m in mains if m.end <= c.start and id(m) in by_fig]
        attached = False
        if cands:
            m = max(cands, key=lambda x: x.end)
            owner = by_fig[id(m)].trig
            if not any(m.end <= t.start < c.start and t.used and t is not owner for t in trigs):
                by_fig[id(m)].changes.append(change)
                c.consumed = True
                attached = True
        if attached:
            continue
        t = _left_trigger(c, trigs, others, text)
        coord = None
        if t is None and c.chained and c.kind == "pct":
            # "more than 80% ... in the second half of 2026 AND more than 70% for the full year 2027": the second
            # figure has no metric phrase of its own and repeats the one before it.
            coord = next((r for r in reversed(recs) if r.fig is not None and r.fig.is_change and r.stat == "growth"
                          and r.trig is not None and r.fig.end <= c.start), None)
            if coord is not None:
                t = coord.trig
        if t is None:
            unclaimed_reasons[id(c)] = "change_without_metric"
            continue
        stat = "growth" if c.kind == "pct" else "change"
        unit = {"money": "USD", "pct": "pct", "bps": "bps", "pp": "pp"}[c.kind]
        rec = _Rec(metric=t.metric, segment=t.segment, stat=stat, unit=unit,
                   lo=c.sign * c.lo, hi=(c.sign * c.hi if c.hi is not None else None),
                   fig=c, trig=t, how="left", qualifier=c.qualifier, basis=c.basis or (coord.basis if coord else None),
                   accounting=_accounting_basis(text, t), growth_inferred=t.growth_rate or coord is not None,
                   figure_text=c.raw, order=c.start,
                   currency_basis="constant" if c.constant_currency else None,
                   segment_source="subject" if t.seg_from_subject else "trigger",
                   after_ok=True, after_limit=next((g.start for g in figs if g.start >= c.end), None))
        if t.seg_from_subject:
            rec.flags.append("segment_from_subject")
        elif t.seg_lower:
            rec.flags.append("segment_lowercase_phrase")
        if t.growth_rate:
            rec.flags.append("metric_inferred_from_growth_rate")
        if coord is not None:
            rec.flags.append("metric_from_coordinated_figure")
        if stat == "growth" and rec.basis is None:
            rec.flags.append("basis_unspecified")
        t.used = True
        c.consumed = True
        recs.append(rec)

    if is_guidance:
        desc_spans: list[tuple[int, int]] = []
        for d in _DESCRIPTOR.finditer(text):
            if any(f.start <= d.start() < f.end for f in figs):
                continue
            best = None
            for t in trigs:
                if t.used or t.end > d.start() or d.start() - t.end > 110:
                    continue
                if not _window_ok(text, t.end, d.start()):
                    continue
                if best is None or t.end > best.end:
                    best = t
            if best is None:
                continue
            best.used = True
            desc_stat = "level" if best.metric in PCT_LEVEL_METRICS else "growth"
            recs.append(_Rec(metric=best.metric, segment=best.segment, stat=desc_stat, unit="pct",
                             lo=None, hi=None, fig=None, trig=best, descriptor=d.group(0).strip(),
                             growth_inferred=best.growth_rate, figure_text=d.group(0).strip(),
                             accounting=_accounting_basis(text, best), order=d.start(),
                             flags=(["metric_inferred_from_growth_rate"] if best.growth_rate else [])))
            desc_spans.append((d.start(), d.end()))
        lead_basis = _lead_basis(text)
        for d in _SEG_DESCRIPTOR.finditer(text):
            if any(a < d.end() and d.start() < b for a, b in desc_spans):
                continue
            for name in [s.strip() for s in re.split(r"\s+and\s+|,", d["segs"]) if s.strip()]:
                seg = _normalize_segment(_TOKEN.findall(name))
                if seg == "total" or seg in _GENERIC_SEGMENT_WORDS:
                    continue
                recs.append(_Rec(metric="revenue", segment=seg, stat="growth", unit="pct", lo=None, hi=None,
                                 fig=None, trig=None, descriptor=d["desc"].lower(), figure_text=d["desc"],
                                 basis=lead_basis, order=d.start(), segment_source="descriptor",
                                 flags=["segment_from_descriptor"]))

    for f in impact_figs:
        fav = _FAVORABLE.search(text)
        unfav = _UNFAVORABLE.search(text)
        direction = None
        if fav and (not unfav or fav.start() < unfav.start()):
            direction = "favorable"
        elif unfav:
            direction = "unfavorable"
        unit = {"money": "USD", "pct": "pct", "bps": "bps", "pp": "pp"}[f.kind]
        recs.append(_Rec(metric="impact", segment=None, stat="impact", unit=unit,
                         lo=f.lo, hi=f.hi, fig=f, trig=None, qualifier=f.qualifier,
                         driver=driver, direction=direction, figure_text=f.raw, order=f.start))
        f.consumed = True

    unclaimed = []
    for f in figs:
        if not f.consumed:
            reason = unclaimed_reasons.get(id(f)) or ("no_metric_phrase" if not trigs else "not_tied_to_metric")
            unclaimed.append({"figure_text": f.raw, "reason": reason, "start": f.start})
    recs.sort(key=lambda r: r.order)
    return recs, unclaimed


# =========================================================================== #
# Document-level extraction
# =========================================================================== #

def _call_period_label(transcript: Transcript) -> str | None:
    counts: Counter = Counter()
    for turn in transcript.turns:
        if turn.role not in (ROLE_MANAGEMENT, ROLE_UNKNOWN) or turn.section != "prepared":
            continue
        for s in turn.sentences:
            if _GUIDE_CUE.search(s.text):
                continue
            for p in _periods_in(s.text):
                if re.match(r"(?:\w+ quarter|q[1-4])", p):
                    counts[p] += 1
    return counts.most_common(1)[0][0] if counts else None


def _confidence(rec: _Rec, turn: Turn) -> str:
    conf = "high"
    if turn.section == "qa":
        conf = _cap_conf(conf, "medium")
    if turn.role == ROLE_UNKNOWN:
        conf = _cap_conf(conf, "low")
    if rec.segment_source == "subject" or "segment_lowercase_phrase" in rec.flags:
        conf = _cap_conf(conf, "medium")
    if rec.growth_inferred or rec.context_from == "previous" or rec.reference:
        conf = _cap_conf(conf, "medium")
    if rec.segment is None and rec.metric != "impact":
        conf = _cap_conf(conf, "low")
    return conf


def extract_facts(transcript: Transcript) -> dict:
    call_label = _call_period_label(transcript)
    facts: list[Fact] = []
    unclaimed: list[dict] = []

    for turn in transcript.turns:
        if turn.role not in (ROLE_MANAGEMENT, ROLE_UNKNOWN):
            continue
        guidance_ctx: str | None = None
        guidance_ctx_at = -99
        prev_guidance_index = -99
        section_seg: str | None = None
        section_at = -99
        prev_last: _Rec | None = None
        prev_index = -2
        for sent in turn.sentences:
            text = sent.text
            is_guidance = bool(_GUIDE_CUE.search(text)) or (
                sent.index - prev_guidance_index == 1 and bool(_GUIDE_CONTINUATION.match(text)))
            if is_guidance:
                prev_guidance_index = sent.index
            if guidance_ctx is not None and sent.index - guidance_ctx_at > _GUIDANCE_CTX_TTL:
                guidance_ctx = None
            distinct_periods = list(dict.fromkeys(_periods_in(text)))
            if (is_guidance or re.search(r"\b(?:outlook|guidance)\b", text, re.I)) and distinct_periods:
                if guidance_ctx not in distinct_periods:
                    guidance_ctx = distinct_periods[-1]
                guidance_ctx_at = sent.index
            elif is_guidance and guidance_ctx is not None:
                guidance_ctx_at = sent.index

            is_head, head_seg = _section_from_heading(text)
            active_section = section_seg if (section_seg and 0 < sent.index - section_at <= _SECTION_TTL) else None
            if is_head:
                section_seg, section_at = head_seg, sent.index

            recs, leftovers = _process_sentence(text, is_guidance, active_section)
            for lo in leftovers:
                unclaimed.append({
                    "figure_text": lo["figure_text"], "reason": lo["reason"], "sentence": text,
                    "speaker": turn.speaker, "role": turn.role, "section": turn.section,
                    "turn_index": turn.index, "ev_start": sent.start,
                })

            last_here: _Rec | None = None
            for rec in recs:
                if rec.metric == "impact":
                    if last_here is not None:
                        rec.context_metric, rec.context_segment = last_here.metric, last_here.segment
                        rec.context_from = "sentence"
                    elif re.search(_GROWTH_RATE_RE.pattern + r"|\b(?:revenue|sales)\s+growth\b", text, re.I):
                        rec.context_metric, rec.context_segment = "revenue", "total"
                        rec.context_from = "sentence"
                        rec.flags.append("context_inferred_from_growth_rate")
                    elif prev_last is not None and sent.index - prev_index == 1:
                        rec.context_metric, rec.context_segment = prev_last.metric, prev_last.segment
                        rec.context_from = "previous"
                        rec.flags.append("context_from_previous_sentence")
                    else:
                        rec.flags.append("context_unresolved")
                    if rec.context_metric == "eps" and rec.unit == "USD":
                        rec.unit = "USD_per_share"
                else:
                    last_here = rec

                kind = "guidance" if (is_guidance or rec.reference) else "reported"
                if rec.metric == "dividends" and re.search(r"\bdeclared\b", text, re.I):
                    kind = "declared"
                if kind == "guidance":
                    period = guidance_ctx
                    if len(distinct_periods) > 1 and rec.after_ok:
                        period = _period_after(text, rec.order, rec.after_limit) or guidance_ctx
                elif kind == "declared":
                    period = None
                else:
                    period = _nearest_period(text, rec.order) or call_label

                sign_sensitive = rec.lo is not None
                fact = Fact(
                    kind=kind, metric=rec.metric, segment=rec.segment, stat=rec.stat,
                    value=rec.lo if sign_sensitive else None,
                    value_high=rec.hi, unit=rec.unit, qualifier=rec.qualifier, basis=rec.basis,
                    accounting=rec.accounting, currency_basis=rec.currency_basis,
                    descriptor=rec.descriptor, driver=rec.driver,
                    direction=rec.direction, context_metric=rec.context_metric,
                    context_segment=rec.context_segment, period=period,
                    changes=[{**c, "ev_start": sent.start, "ev_end": sent.end} for c in rec.changes],
                    speaker=turn.speaker, role=turn.role, section=turn.section,
                    turn_index=turn.index, sentence_index=sent.index, sentence=text,
                    ev_start=sent.start, ev_end=sent.end, figure_text=rec.figure_text,
                    flags=list(rec.flags), confidence=_confidence(rec, turn),
                )
                if kind != "declared" and period is None:
                    fact.flags.append("period_unresolved")
                    fact.confidence = _cap_conf(fact.confidence, "medium")
                if turn.role == ROLE_UNKNOWN:
                    fact.flags.append("speaker_role_unknown")
                facts.append(fact)
            if last_here is not None:
                prev_last, prev_index = last_here, sent.index

    facts = _dedupe(facts)
    for f in facts:
        problems = verify_fact(f, transcript.text)
        f.verified = not problems
        f.flags.extend(problems)
    _flag_conflicts(facts)
    stem = "_".join(str(x) for x in (transcript.meta.get("ticker"),
                                     transcript.meta.get("fiscal_year"),
                                     transcript.meta.get("fiscal_quarter")) if x) or "call"
    for i, f in enumerate(facts, 1):
        f.id = f"{stem}-{i:03d}"

    unclaimed = _dedupe_unclaimed(unclaimed)
    checks = run_checks(facts)
    meta = dict(transcript.meta)
    meta["reported_period_label"] = call_label
    meta["source_file"] = transcript.filename
    stats = _stats(facts, unclaimed, checks)
    return {"meta": meta, "facts": [asdict(f) for f in facts], "unclaimed": unclaimed,
            "checks": checks, "stats": stats}


def _fact_key(f: Fact) -> tuple:
    return (f.kind, f.metric, f.segment, f.stat, f.period, f.unit, f.value, f.value_high,
            f.descriptor, f.driver, f.basis, f.accounting, f.currency_basis,
            f.context_metric, f.context_segment)


def _dedupe(facts: list[Fact]) -> list[Fact]:
    groups: dict[tuple, list[Fact]] = defaultdict(list)
    order: list[tuple] = []
    for f in facts:
        k = _fact_key(f)
        if k not in groups:
            order.append(k)
        groups[k].append(f)
    out = []
    for k in order:
        g = groups[k]
        canon = max(g, key=lambda x: (_CONF_RANK[x.confidence], bool(x.changes), -x.ev_start))
        # The same level is often stated twice ("...grew 23% year-over-year to $3.1 billion" by the CEO, "...$3.1 billion,
        # up 23% year-over-year and 6% sequentially" by the CFO): keep every distinct change, each with its own evidence.
        seen = {(c["value"], c["unit"], c["basis"]) for c in canon.changes}
        for other in g:
            for c in other.changes:
                if other is not canon and (c["value"], c["unit"], c["basis"]) not in seen:
                    seen.add((c["value"], c["unit"], c["basis"]))
                    canon.changes.append(c)
        canon.mentions = len(g)
        out.append(canon)
    out.sort(key=lambda f: f.ev_start)
    return _merge_undated(out)


def _merge_undated(facts: list[Fact]) -> list[Fact]:
    """A fact with no period that is identical to exactly one dated fact is the same statement, repeated."""
    def key_wo_period(f):
        k = list(_fact_key(f))
        k[4] = None
        return tuple(k)

    dated: dict[tuple, list[Fact]] = defaultdict(list)
    for f in facts:
        if f.period is not None:
            dated[key_wo_period(f)].append(f)
    keep = []
    for f in facts:
        twins = dated.get(key_wo_period(f)) if f.period is None else None
        if twins and len(twins) == 1:
            twins[0].mentions += f.mentions
            continue
        keep.append(f)
    return keep


def _dedupe_unclaimed(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for u in items:
        k = (u["figure_text"], u["sentence"])
        if k in seen:
            continue
        seen.add(k)
        out.append(u)
    return out


def _flag_conflicts(facts: list[Fact]) -> None:
    groups: dict[tuple, list[Fact]] = defaultdict(list)
    for f in facts:
        if f.stat in ("level", "growth") and f.descriptor is None and f.value is not None:
            groups[(f.kind, f.metric, f.segment, f.stat, f.period, f.unit, f.basis, f.accounting,
                    f.currency_basis)].append(f)
    for g in groups.values():
        if len({(x.value, x.value_high) for x in g}) > 1:
            for f in g:
                f.flags.append("conflict_with_other_value")
                f.confidence = _cap_conf(f.confidence, "low")


# =========================================================================== #
# Verification and consistency checks
# =========================================================================== #

_NUM_WITH_SCALE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s?(trillion|billion|million|thousand|bn|mm|mn|[BMK])?(?![A-Za-z])", re.I)


def _independent_numbers(figure_text: str, money: bool) -> list[float]:
    """Re-derive numbers from the raw figure text without using the extractor's regexes."""
    pairs = [(float(n.replace(",", "")), s) for n, s in _NUM_WITH_SCALE.findall(figure_text)]
    if not money:
        return [n for n, _ in pairs]
    scales = [_SCALE[s.lower()] for _, s in pairs if s]
    fallback = scales[0] if scales else 1.0
    return [n * (_SCALE[s.lower()] if s else fallback) for n, s in pairs]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def verify_fact(f: Fact, text: str) -> list[str]:
    """Independent re-check of a fact against the source text. Returns a list of problems."""
    problems = []
    if text[f.ev_start:f.ev_end] != f.sentence:
        problems.append("verify:offset_mismatch")
    if f.figure_text and f.figure_text not in f.sentence:
        problems.append("verify:figure_not_in_sentence")
    for ch in f.changes:
        evidence = text[ch["ev_start"]:ch["ev_end"]] if "ev_start" in ch else f.sentence
        if ch["text"] not in evidence:
            problems.append("verify:change_not_in_sentence")
    if f.value is not None and f.figure_text:
        nums = _independent_numbers(f.figure_text, money=f.unit in ("USD", "USD_per_share"))
        if nums:
            if not _close(abs(f.value), nums[0]):
                problems.append("verify:value_mismatch")
            if f.value_high is not None and (len(nums) < 2 or not _close(abs(f.value_high), nums[-1])):
                problems.append("verify:range_mismatch")
    return problems


def run_checks(facts: list[Fact]) -> list[dict]:
    checks: list[dict] = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    failed = [f.id or f.figure_text for f in facts if not f.verified]
    add("evidence_verification", "pass" if not failed else "fail",
        "all facts trace to the transcript text" if not failed else f"{len(failed)} facts failed: {failed[:5]}")

    bad_range = [f for f in facts if f.value is not None and f.value_high is not None and f.value > f.value_high]
    add("range_order", "pass" if not bad_range else "fail",
        "all ranges are low<=high" if not bad_range else f"{len(bad_range)} inverted ranges")

    out_of_bounds = []
    for f in facts:
        if f.unit == "pct" and f.value is not None and f.stat == "level":
            lo, hi = (0, 100) if f.metric == "tax_rate" else (-100, 100)
            if not (lo <= f.value <= hi):
                out_of_bounds.append(f.figure_text)
    add("percent_bounds", "pass" if not out_of_bounds else "fail",
        "margin/tax-rate levels within plausible bounds" if not out_of_bounds else f"out of range: {out_of_bounds}")

    conflicts = [f for f in facts if "conflict_with_other_value" in f.flags]
    add("no_conflicting_values", "pass" if not conflicts else "warn",
        "no metric has two different values in the same period" if not conflicts
        else f"{len(conflicts)} facts share a key with different values")

    rev = {}
    for f in facts:
        if f.kind == "reported" and f.metric == "revenue" and f.stat == "level" and f.value is not None:
            rev.setdefault((f.period, f.segment), f.value)
    by_period: dict = defaultdict(dict)
    for (period, seg), v in rev.items():
        by_period[period][seg] = v
    for period, segs in by_period.items():
        label = period or "unlabeled period"
        if {"total", "products", "services"} <= segs.keys():
            diff = abs(segs["products"] + segs["services"] - segs["total"]) / segs["total"]
            add(f"products_plus_services[{label}]", "pass" if diff <= 0.006 else "warn",
                f"products+services vs total differ by {diff:.2%}")
        parts = {k: v for k, v in segs.items() if k not in ("total", "products")}
        if "total" in segs and len(parts) >= 3:
            diff = abs(sum(parts.values()) - segs["total"]) / segs["total"]
            add(f"segment_sum[{label}]", "pass" if diff <= 0.015 else "warn",
                f"{len(parts)} segments sum to within {diff:.2%} of total (informational: segments may overlap)")
    return checks


def _stats(facts: list[Fact], unclaimed: list[dict], checks: list[dict]) -> dict:
    return {
        "facts": len(facts),
        "by_kind": dict(Counter(f.kind for f in facts)),
        "by_confidence": dict(Counter(f.confidence for f in facts)),
        "verified": sum(1 for f in facts if f.verified),
        "unverified": sum(1 for f in facts if not f.verified),
        "unclaimed_figures": len(unclaimed),
        "checks_failed": sum(1 for c in checks if c["status"] == "fail"),
        "checks_warn": sum(1 for c in checks if c["status"] == "warn"),
    }

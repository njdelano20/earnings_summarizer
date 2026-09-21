"""
signals.py
Deterministic, evidence-linked extraction of *qualitative* signals from management speech: momentum,
product developments, competitive position, pressures, strategy, outlook and management's stance.

One Signal = one management sentence, tagged with
    topics      momentum | product | competition | pressure | strategy | leadership   (primary first)
    horizon     current | forward
    direction   positive | negative | mixed | neutral      (of the business news, not of the speaker's mood)
    stance      confident | cautious | mixed | None       (the speaker's own attitude: lexical, indicative)
    revision    raised | lowered | revised | maintained | None   (what management says about its own view)
    segments    registry node ids the sentence is about (same sentence, or carried from a nearby sentence)
    fact_ids    ledger facts anchored in the same sentence
    cues        the exact phrases that fired each tag, so every judgement is auditable
and always carries the sentence text + character offsets, verified against the transcript (verify_signal).

Same discipline as facts.py: management speech only (analysts, operator and IR are never mined), nothing is
invented (extractive), and anything shaky is flagged and confidence-capped instead of silently trusted.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field, asdict

from facts import _DESCRIPTOR
from segments import Registry, TOTAL
from transcript import Transcript, ROLE_MANAGEMENT, ROLE_UNKNOWN


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


# =========================================================================== #
# Cue lexicons
# =========================================================================== #

_BOILERPLATE = _rx(r"forward-looking|risk factors|safe harbor|form 10-[kq]|form 8-k|assumes no obligation|"
                   r"securities litigation|a replay of|non-gaap (?:measures|reconciliation)")
_PLEASANTRY = _rx(r"^(?:thank(?:s| you)|good (?:afternoon|morning|evening)|hey|hi|yeah|yep|sure|okay|welcome|"
                  r"i (?:really )?appreciate|great question|that's a (?:good|great) question|no problem)\b")

# --- business direction ---------------------------------------------------- #
_POS_STRONG = _rx(
    r"(?<!of )\b(?:record|records|all-time (?:high|highs|record|records)|new all-time|strong(?:er|est)?|strength|solid|healthy|"
    r"robust|accelerat\w*|outperform\w*|exceed\w*|gained|gaining|taking share|winning|resonat\w*|popular|"
    r"(?:remarkably )?better than|positive|favorable|tailwinds?|extraordinary|phenomenal|incredible|impressive|"
    r"highest|best|beyond (?:our )?expectations?|high levels? of demand|double[- ]digit|surpass\w*|"
    r"uniquely|unique differentiator|differentiator|advantaged|weapon|sets? \w+ apart|momentum|displac\w+|unveil\w*|"
    r"all-new|launched|(?:low|mid|high|upper)[- ]single[- ]digits?|(?:low|mid|high)[- ]teens|"
    r"doubl(?:e|ed|es|ing)(?![- ]digit)|tripl(?:e|ed|es|ing)|strategic partnership|"
    r"expanded our (?:long-standing )?partnership|deploy\w*\s+(?:\w+\s+){0,2}at scale)\b")
# The bare noun "growth" is deliberately absent: "impact to our overall growth" is not good news.
_POS_GROWTH = _rx(r"\b(?:grew|grow|grows|growing|expan\w+|improv\w+|up (?:over |about |approximately |a strong )?\d[\d.,]*"
                  r"|increas(?:ed|es|ing)\s+(?:by\s+|over\s+|about\s+|approximately\s+)?\d[\d.,]*"
                  r"|(?:with|delivered|saw|reflecting)\s+(?:(?:sequential|quarter-over-quarter|continued|organic|year-over-year|"
                  r"strong|solid)[\s-]+){0,3}growth|growth\s+of\s+(?:over\s+|about\s+)?\d)\b")
_NEG = _rx(
    r"\b(?:declin\w*|decreas\w*|down (?:over |about |approximately )?\d[\d.,]*|weak\w*|soft(?:er|ness)|pressures?|"
    r"headwinds?|constraints?|difficult|challeng\w*|tighten\w*|slow(?:er|down|ing)?|decelerat\w*|worse|"
    r"delay\w*|uncertain\w*|unclear|inflation|higher (?:memory |component )?(?:costs?|prices?)|reluctantly|dilutive|"
    r"shortfall|unfavorable|not been able|reduc(?:ed|tion)|increasing impact|less flexibility|limited flexibility|"
    r"scrambl\w*|impacted by|(?:supply|capacity)\b[^.,;]{0,40}\btight\b)\b")
_COSTY =_rx(r"\b(?:expenses?|costs?|debt|charges?|opex)\b")
_CONCESSION = _rx(r",?\s+(?:despite|partially offset by|offset by|albeit|although|even though|notwithstanding)\b")

# --- topics ------------------------------------------------------------------ #
_PERF = _rx(r"\b(?:revenue|sales|bookings?|signings|backlog|book-to-bill|ARR|growth|grew|grow(?:ing|s)?|record|"
            r"all-time|install(?:ed)? base|paid subscriptions|upgraders|customers|units|volume|demand|traction|"
            r"momentum|cash flow|margin|profit\w*|earnings|viewership|adoption|accelerat\w+|declin\w+|decreas\w+|"
            r"softness|weakness|EBITDA)\b")
_PRODUCT = _rx(
    r"\b(?:launch\w*|unveil\w*|announc\w*|introduc\w*|releas\w*|roll(?:ed|ing|s)?\s*out|rollout|beta|lineup|all-new|"
    r"new\s+(?:[\w-]+\s+)?(?:feature|product|offering|model|program|platform|capabilit\w+|generation|version|system|"
    r"tool|chip|processor|service|partnership|agreement)s?|next[- ]generation|roadmap|availability|"
    r"ship(?:ping|ped)?|deploy\w*|adoption|partner(?:ship|ships|ed|ing)?|collaborat\w+|integrat\w+|embed\w*|"
    r"innovat\w+|approval to ship|reception|reviews?|feedback|use cases?|customer satisfaction|installed|"
    r"resonat\w+|popular)\b")
_COMP = _rx(
    r"\b(?:(?:gain(?:ed|s|ing)?|tak(?:e|es|ing|en)|won|win(?:s|ning)?|lost|los(?:e|es|ing))\s+(?:[\w-]+\s+){0,3}?share|"
    r"(?:better|faster)\s+than\s+the\s+market|outgrow\w*\s+the\s+market|"
    r"market\s+share|share\s+(?:gains?|loss(?:es)?)|displac\w+|compet(?:e|es|ing|itors?|ition|itive(?:ly)?)|"
    r"differentiat\w+|advantag\w+|uniquely|unique\s+(?:combination|differentiator|position)|"
    r"sets?\s+(?:us|apple|\w+)\s+apart|weapon|outperform\w*\s+the\s+market|winning\s+in|industry standard|"
    r"leading\s+(?:position|provider)|ahead\s+of\s+(?:the\s+)?(?:market|peers|competit\w+))\b")
_STRATEGY = _rx(r"\b(?:acqui\w+|divest\w+|M&A|capital allocation|dividend\w*|repurchase\w*|buyback|invest(?:ing|ed|ment|ments)?|"
                r"manufacturing (?:program|commitment)|commitment|R&D|restructur\w+|workforce rebalancing|synerg\w+|"
                r"accretive|accretion|sale of|disciplined)\b")
_PRESSURE = _rx(r"\b(?:headwinds?|supply constraints?|constraints?|pressures?|tighten\w*|soft(?:er|ness)|weakness|uncertain\w*|"
                r"inflation|memory (?:cost|pric)\w*|higher (?:memory |component )?(?:costs?|prices?)|court ruling|"
                r"(?:memory|component)(?: and (?:memory|component))? costs?|(?:supply|capacity)\b[^.,;]{0,40}\btight\b|"
                r"lower\s+(?:[\w-]+\s+){0,2}(?:sales|demand|volumes?|shipments|orders)|"
                r"regulat\w+|tariffs?|difficult|slower|delay\w*|raised prices|less flexibility|not been able to)\b")
_LEADERSHIP = _rx(r"\b(?:final earnings call|my final|incoming ceo|new role|leads? these calls|lead these calls|"
                  r"succession|successor|new ceo|new cfo)\b")

# --- horizon / revision ------------------------------------------------------ #
_FWD = _rx(
    r"\b(?:we|i)\s+(?:currently\s+|now\s+|also\s+|still\s+|do\s+|would\s+|continue\s+to\s+)*"
    r"(?:project|forecast|believe|plan|hope|should\s+see|will|are\s+going\s+to|'re\s+going\s+to|"
    r"see\s+(?:the|that|growth|revenue|mid|full|market|even)|'ll)\b"
    r"|(?<!than )(?<!as )\b(?:expect|anticipate)(?:s|ing)?\b|\banticipat(?:e|es|ing)\b"
    r"|\bexpected\s+to\b|\b(?:is|are)\s+expected\b|\bgoing\s+forward\b|\bnext\s+(?:quarter|year|week)\b"
    r"|\blater\s+this\s+year\b|\bthis\s+fall\b|\bby\s+(?:the\s+)?end\s+of\b|\bbeyond\s+(?:september|december|the|20\d\d|this)\b"
    r"|\b(?:for|through(?:out)?)\s+the\s+(?:full\s+)?year\b|\bfull[- ]year\b|\bsecond\s+half\b|\bin\s+the\s+(?:coming|next)\b"
    r"|\byet\s+to\s+come\b|\bfuture\b|\b(?:outlook|guidance)\b|\bwell[- ]positioned\b|\bnever\s+been\s+more\s+"
    r"(?:confident|optimistic)\b|\bcontinue\s+or\s+accelerate\b|\bmoving\s+forward\b|\bahead\b(?=.{0,30}\b(?:quarter|year)\b)"
    r"|\bconfidence\s+in\s+our\s+(?:next|full)"
    r"|\bon\s+track\s+to\b|\b(?:is|are)\s+(?:going|set|slated|scheduled)\s+to\b|\bbeginning\s+(?:later|next|in)\b"
    r"|\b(?:business|segment|portfolio|market|revenue|growth|margin|demand|supply)\s+(?:will|should)\s+(?:be\s+)?"
    r"(?:give|drive|deliver|contribute|grow|continue|see|benefit|provide|help|add|improve|remain|ramp|start|begin|better)\b")
# "worse than 90 days ago" is deliberately not 'lowered': it is a bigger headwind, not a stated guidance cut.
_REV_LOWERED = _rx(r"\b(?:lower(?:ed|ing)|cut(?:ting)?|taking\s+down)\s+(?:our\s+)?(?:outlook|guidance|expectations?|forecast|view)\b")
_REV_RAISED = _rx(r"\b(?:rais(?:e|ed|ing)|increas(?:e|ed|ing))\s+(?:our\s+)?(?:outlook|guidance|expectations?|forecast|view)\b")
_REV_NOW = _rx(r"\bnow\s+(?:see|expect|anticipate|project)\b")
_REV_MAINTAINED = _rx(r"\b(?:hold(?:ing)?\s+(?:our|the)\s+view|maintain(?:ing)?\s+(?:our|the)\s+(?:view|guidance|outlook)|"
                      r"reaffirm\w*|consistent\s+with\s+(?:our\s+view|what\s+we\s+said)|unchanged|"
                      r"continue\s+to\s+expect|still\s+expect\w*)\b")

# --- stance (the speaker's own attitude) ------------------------------------- #
_CONF = _rx(r"\b(?:confiden(?:t|ce)|excit\w+|thrilled|pleased|optimistic|enthusias\w+|phenomenal|couldn't be more|"
            r"never been more|off the charts|extremely well|tremendous\w*|delighted|comfortable|bullish|convinced|"
            r"gratifying|wonderful|proud|couldn't be happier|(?:anything|nothing)\s+(?:that\s+)?would\s+give\s+us\s+pause|"
            r"feel\s+(?:very\s+|really\s+)?(?:good|great|comfortable)\s+about)\b")
_HEDGE = _rx(r"\b(?:unclear|uncertain\w*|too early|early going|early days|we'll see|we will see|not (?:obvious|sure|certain|clear)|"
             r"difficult to (?:say|predict)|hard to (?:say|predict)|prudent\w*|monitor\w*|cautious\w*|evaluat\w+|"
             r"remains to be seen|don't want to say|no guarantee|limited flexibility|less flexibility|"
             r"will\s+depend\s+(?:on|upon)|watching\s+(?:the\s+)?(?:\w+\s+){0,2}costs?|plays?\s+out)\b")
_WITHHELD = _rx(r"\b(?:not providing (?:any )?(?:kind of )?(?:color|guidance)|we're not providing|not going to (?:comment|speculate|get into)|"
                r"focused on our (?:own )?plans|no comment|we don't (?:provide|break out))\b")

# --- guided growth phrases (qualitative guidance the ledger does not capture yet) ------ #
_BANDS = {("low", "single"): (1.0, 3.0), ("mid", "single"): (4.0, 6.0), ("high", "single"): (7.0, 9.0),
          ("upper", "single"): (7.0, 9.0), ("low", "teens"): (10.0, 13.0), ("mid", "teens"): (14.0, 16.0),
          ("high", "teens"): (17.0, 19.0), ("low", "twenties"): (20.0, 23.0), ("mid", "twenties"): (24.0, 26.0),
          ("high", "twenties"): (27.0, 29.0)}
_ABOVE_HIGH_END = _rx(r"\b(?:slightly\s+)?above\s+the\s+high\s+end\s+of\b")
_LOW_END = _rx(r"\b(?:at\s+the\s+)?low\s+end\b")
_HIGH_END = _rx(r"\b(?:at\s+the\s+)?high\s+end\b")
_GROWTH_WORDS = _rx(r"\b(?:revenue|sales|growth|grow)\b")
_DIR_UP = _rx(r"\baccelerat(?:e|es|ed|ing|ion)\b")        # not "accelerator(s)" (AMD's data center AI accelerators)
_MARKET_TALK = _rx(r"\b(?:market|TAM)\b")
_DIR_DOWN = _rx(r"\b(?:decline|declining|decrease|contract)\b")
_DIR_FLAT = _rx(r"\b(?:consistent\s+with|similar\s+to|in\s+line\s+with)\b")
_PRONOUN_LEAD = _rx(r"^(?:that|this|it|these|those|they|which|and|but|so)\b")
# Process talk ("Let me walk through...") carries no business content unless it also carries a number.
_PROCEDURAL = _rx(r"^(?:let me|let's|why don't i|i'll let|i'll just|i'd like to|i just wanted|i wanted to|"
                  r"kevan, you want|i(?:'ll| will) (?:start|begin) with|(?:now,? )?i(?:'ll| will) (?:now )?(?:turn|hand)|"
                  r"with that,? i)\b")
# A sentence about the whole company must not inherit a segment from a neighbouring sentence.
_TOTAL_SCOPE = _rx(r"\b(?:company|total|overall|consolidated|our (?:revenue|gross margin|earnings|net income)|"
                   r"earnings per share|operating (?:cash flow|expenses))\b")
_NEGATED = _rx(r"(?:\bnot|n't|\bno|\bnever|\bwithout)\s+(?:\w+\s+){0,2}$")

_LAUNCH_STEMS = ("unveil", "launch", "announc", "introduc", "releas", "rollout", "roll out", "rolled out", "approval to ship")
_DIM_RANK = {"segment": 0, "geography": 1, "customer": 2, "initiative": 3, "driver": 4}
_TOPIC_WEIGHT = {"competition": 3.0, "product": 2.5, "pressure": 2.5, "strategy": 2.0, "momentum": 2.0, "leadership": 1.5}
_TOPIC_ORDER = ["competition", "product", "pressure", "strategy", "momentum", "leadership"]
_CARRY_WINDOW = 3
_HEADLINE_METRICS = {"revenue", "eps", "free_cash_flow", "operating_income", "net_income"}


# =========================================================================== #
# Model
# =========================================================================== #

@dataclass
class Signal:
    id: str = ""
    topics: list[str] = field(default_factory=list)
    horizon: str = "current"
    direction: str = "neutral"
    stance: str | None = None
    withheld: bool = False
    revision: str | None = None
    segments: list[str] = field(default_factory=list)
    home: str | None = None                 # node the snapshot files this signal under (None = company-wide)
    fact_ids: list[str] = field(default_factory=list)
    guided: dict | None = None
    cues: dict = field(default_factory=dict)
    score: float = 0.0
    speaker: str = ""
    role: str = ""
    section: str = ""
    turn_index: int = 0
    sentence_index: int = 0
    sentence: str = ""
    ev_start: int = 0
    ev_end: int = 0
    confidence: str = "high"
    flags: list[str] = field(default_factory=list)
    verified: bool = False


def _hits(rx: re.Pattern, text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).lower() for m in rx.finditer(text)))


def _main_clause(text: str) -> str:
    m = _CONCESSION.search(text)
    return text[:m.start()] if m and m.start() > 15 else text


def _positive_hits(rx: re.Pattern, text: str) -> list[str]:
    """Like _hits, but a cue that is negated a few words earlier ('didn't see acceleration') does not count."""
    out = []
    for m in rx.finditer(text):
        if _NEGATED.search(text[max(0, m.start() - 24):m.start()]):
            continue
        out.append(m.group(0).lower())
    return list(dict.fromkeys(out))


# "operating income was $582 million or 15% of revenue compared to $767 million or 21% a year ago": the comparison
# itself is the news, whatever adjectives surround it. Only where higher is better (never costs or taxes).
_COMPARED_PCT = _rx(r"(\d+(?:\.\d+)?)%\s+(?:of\s+\w+\s+)?(?:compared\s+(?:to|with)|versus|vs\.?)\s+[^.]{0,60}?"
                    r"(\d+(?:\.\d+)?)%\s+(?:a\s+year\s+ago|last\s+year|in\s+the\s+(?:prior|year[- ]ago)\b)")
_COMPARED_USD = _rx(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|million)?\s+(?:compared\s+(?:to|with)|versus|vs\.?)\s+"
                    r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|million)?\s+(?:a\s+year\s+ago|last\s+year)")
_HIGHER_IS_BETTER = _rx(r"\b(?:income|margin|revenue|sales|profit|earnings|cash\s+flow|eps)\b")


def _numeric_direction(text: str) -> str | None:
    if _COSTY.search(text) or re.search(r"\btax\b", text, re.I) or not _HIGHER_IS_BETTER.search(text):
        return None
    scale = {"billion": 1e9, "million": 1e6, None: 1.0}
    m = _COMPARED_PCT.search(text)
    if m:
        now, then = float(m.group(1)), float(m.group(2))
    else:
        m = _COMPARED_USD.search(text)
        if not m:
            return None
        now = float(m.group(1).replace(",", "")) * scale[(m.group(2) or "").lower() or None]
        then = float(m.group(3).replace(",", "")) * scale[(m.group(4) or "").lower() or None]
    return "positive" if now > then else "negative" if now < then else None


def _direction(text: str) -> tuple[str, dict]:
    main = _main_clause(text)
    pos = _positive_hits(_POS_STRONG, main)
    if not _COSTY.search(main):
        pos += _positive_hits(_POS_GROWTH, main)
    neg = _hits(_NEG, main)
    cues = {}
    compared = _numeric_direction(main)
    if compared:
        cues["comparison"] = [compared]
        return compared, cues
    if pos:
        cues["positive"] = pos
    if neg:
        cues["negative"] = neg
    if pos and neg:
        return "mixed", cues
    return ("positive" if pos else "negative" if neg else "neutral"), cues


def _guided(text: str, node_ids: list[str], registry: Registry) -> dict | None:
    """Qualitative growth guidance in a forward sentence ('mid-single-digit', 'to decline', 'acceleration')."""
    if not _GROWTH_WORDS.search(text):
        return None
    # "we now expect the data center AI accelerator market to grow more than 45%" is the market, not the company's revenue
    if _MARKET_TALK.search(text) and not re.search(r"\b(?:revenue|sales)\b", text, re.I):
        return None
    desc = _DESCRIPTOR.search(text)
    band = position = modifier = None
    descriptor = None
    if desc:
        descriptor = desc.group(0).lower()
        m = re.match(r"(low|mid|high|upper|lower)[- ]?(single|double|teens|twenties)", descriptor)
        if m:
            key = (m.group(1), m.group(2))
            band = _BANDS.get(key)
        if band is not None:
            if _ABOVE_HIGH_END.search(text):
                band, modifier = (band[1], band[1] + 1.0), "slightly_above_high_end"
            elif _LOW_END.search(text):
                band, position = (band[0], band[0] + (band[1] - band[0]) / 2), "low_end"
            elif _HIGH_END.search(text):
                band, position = (band[1] - (band[1] - band[0]) / 2, band[1]), "high_end"
    word = None
    if _DIR_UP.search(text):
        word = "up"
    elif _DIR_DOWN.search(text):
        word = "down"
    elif band is None and _DIR_FLAT.search(text):
        word = "flat"
    if band is None and word is None:
        return None
    seg_nodes = [n for n in node_ids if registry.get(n).dimension == "segment"]
    return {"node": seg_nodes[0] if seg_nodes else TOTAL, "metric": "revenue", "descriptor": descriptor,
            "band": list(band) if band else None, "position": position, "modifier": modifier,
            "direction_word": word, "source": "signal_text"}


def _home(node_ids: list[str], registry: Registry) -> str | None:
    """
    Node the sentence is filed under (node_ids arrive in order of first mention, i.e. the grammatical subject first):
      * a node that is the parent of another matched node wins (a sentence listing a segment's parts is about the segment)
      * two sibling sub-segments -> their parent
      * three or more geographies -> company-wide (None)
      * otherwise the first-mentioned node of the highest-ranked dimension.
    """
    if not node_ids:
        return None
    best_dim = min(_DIM_RANK.get(registry.get(n).dimension, 9) for n in node_ids)
    cands = [n for n in node_ids if _DIM_RANK.get(registry.get(n).dimension, 9) == best_dim]
    for n in cands:
        if any(m != n and n in registry.ancestors(m) for m in cands):
            return n
    parents: dict[str, list[str]] = {}
    for n in cands:
        p = registry.get(n).parent
        if p:
            parents.setdefault(p, []).append(n)
    for p, kids in parents.items():
        if len(kids) >= 2:
            return p
    if best_dim == _DIM_RANK["geography"] and len(cands) >= 3:
        return None
    return cands[0]


def _score(sig: Signal, text: str) -> float:
    primary = sig.topics[0] if sig.topics else None
    s = _TOPIC_WEIGHT.get(primary, 0.5)
    s += 0.5 if sig.fact_ids else 0.0          # the figure is already in the fact table; the sentence adds the "why"
    s += 1.0 if sig.horizon == "forward" else 0.0
    s += 1.5 if sig.cues.get("guidance_figure") else 0.0    # a stated headline guide is the core of "where it is heading"
    s += 1.0 if sig.horizon == "forward" and sig.direction in ("negative", "mixed") else 0.0   # a guided headwind
    if any(c.startswith(_LAUNCH_STEMS) for c in sig.cues.get("product", [])):
        s += 1.0                                # an event (unveiled, launched, announced) outranks commentary about it
    s += 1.0 if sig.revision else 0.0
    s += 1.0 if sig.guided else 0.0
    s += 0.5 if sig.direction in ("negative", "mixed") else 0.0     # bad news is at least as informative as good
    s += 0.5 * min(3, len(sig.topics))
    s += 0.5 if re.search(r"\d", text) else 0.0
    s += 0.5 if sig.stance else 0.0
    s -= 1.0 if "context_dependent" in sig.flags else 0.0
    s -= 1.0 if len(text) > 300 else 0.5 if len(text) > 200 else 0.0     # short, specific claims beat long marketing ones
    return round(s, 2)


_NOT_PROPER = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
               "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "the", "and", "our",
               "but", "for", "with", "that", "this", "when", "what", "there", "then", "they", "these"}


def _add_salience(sigs: list[Signal]) -> None:
    """Things management keeps coming back to (a product name repeated across the call) matter more than one-offs."""
    proper_by_sig, tf = [], Counter()
    for sig in sigs:
        words = re.findall(r"[A-Za-z0-9+&'-]+", sig.sentence)[1:]
        proper = {w for w in words if w[0].isupper() and len(w) >= 3 and w.lower() not in _NOT_PROPER}
        proper_by_sig.append(proper)
        tf.update(proper)
    for sig, proper in zip(sigs, proper_by_sig):
        sal = sum(math.log(tf[w]) for w in proper if tf[w] >= 3)
        sig.score = round(sig.score + min(1.0, 0.2 * sal), 2)


# =========================================================================== #
# Extraction
# =========================================================================== #

def extract_signals(transcript: Transcript, registry: Registry, facts: list[dict]) -> list[dict]:
    fact_by_sentence: dict[tuple[int, int], list[str]] = {}
    fact_segment: dict[str, str | None] = {}
    headline_guides: set[str] = set()
    for f in facts:
        fact_by_sentence.setdefault((f["turn_index"], f["sentence_index"]), []).append(f["id"])
        if f["confidence"] != "low":
            fact_segment[f["id"]] = f["segment"]
            if f["kind"] == "guidance" and f["value"] is not None and f["segment"] == TOTAL \
                    and f["metric"] in _HEADLINE_METRICS:
                headline_guides.add(f["id"])

    stem = "_".join(str(x) for x in (transcript.meta.get("ticker"), transcript.meta.get("fiscal_year"),
                                     transcript.meta.get("fiscal_quarter")) if x) or "call"
    out: list[Signal] = []
    for turn in transcript.turns:
        if turn.role not in (ROLE_MANAGEMENT, ROLE_UNKNOWN):
            continue
        carry_nodes: list[str] = []
        carry_index = -99
        for sent in turn.sentences:
            text = sent.text
            words = len(text.split())
            if words < 3 or text.endswith("?") or _BOILERPLATE.search(text) or _PLEASANTRY.search(text):
                continue
            if _PROCEDURAL.search(text) and not re.search(r"\d", re.sub(r"\b(?:19|20)\d\d\b|\bQ[1-4]\b", "", text)):
                continue                       # (a year or quarter tag is not a business figure)

            nodes = registry.nodes_in_text(text)
            core = [n for n in nodes if registry.get(n).dimension != "driver"]
            direction, dir_cues = _direction(text)
            fwd = _hits(_FWD, text)
            horizon = "forward" if fwd else "current"
            cues: dict = dict(dir_cues)
            if fwd:
                cues["forward"] = fwd

            topics = []
            comp, prod, strat = _hits(_COMP, text), _hits(_PRODUCT, text), _hits(_STRATEGY, text)
            # A pressure named only inside a concession ("...record, despite supply constraints") is not the news.
            press, lead = _hits(_PRESSURE, _main_clause(text)), _hits(_LEADERSHIP, text)
            perf = _hits(_PERF, text)
            if comp:
                topics.append("competition"); cues["competition"] = comp
            if prod:
                topics.append("product"); cues["product"] = prod
            if press and direction != "positive":
                topics.append("pressure"); cues["pressure"] = press
            if strat:
                topics.append("strategy"); cues["strategy"] = strat
            if direction != "neutral" and (perf or (core and not comp and not prod)):
                topics.append("momentum"); cues["performance"] = perf
            if lead:
                topics.append("leadership"); cues["leadership"] = lead
            for n in nodes:
                nt = registry.get(n).topic
                if nt and nt not in topics:
                    topics.append(nt); cues.setdefault("node_topic", []).append(f"{n}:{nt}")
            topics.sort(key=_TOPIC_ORDER.index)

            conf_c, hedge_c, withheld_c = _hits(_CONF, text), _hits(_HEDGE, text), _hits(_WITHHELD, text)
            stance = None
            if conf_c and (hedge_c or withheld_c):
                stance = "mixed"
            elif conf_c:
                stance = "confident"
            elif hedge_c or withheld_c:
                stance = "cautious"
            if conf_c:
                cues["confident"] = conf_c
            if hedge_c:
                cues["hedge"] = hedge_c
            if withheld_c:
                cues["withheld"] = withheld_c

            revision = None
            if _REV_LOWERED.search(text):
                revision = "lowered"
            elif _REV_RAISED.search(text):
                revision = "raised"
            elif _REV_NOW.search(text):
                revision = "revised"
            elif _REV_MAINTAINED.search(text):
                revision = "maintained"
            if revision:
                cues["revision"] = [revision]

            if not topics and not stance and not (horizon == "forward" and (perf or nodes)):
                continue

            flags: list[str] = []
            seg_nodes = list(nodes)
            # Only qualitative statements borrow a segment from a neighbouring sentence (share gains, reception,
            # a bare "early going for us"). Figures name their own subject, and company-wide sentences never borrow.
            if (not core and carry_nodes and sent.index - carry_index <= _CARRY_WINDOW
                    and not _TOTAL_SCOPE.search(text)
                    and (set(topics) & {"competition", "product"} or (stance and not topics))):
                seg_nodes = nodes + [n for n in carry_nodes if n not in nodes]
                flags.append("segment_from_context")
            if core:
                seg_src = [n for n in core if registry.get(n).dimension in ("segment", "initiative")]
                if seg_src:
                    carry_nodes, carry_index = seg_src[:1], sent.index

            fact_ids = fact_by_sentence.get((turn.index, sent.index), [])
            # A figure names its own subject: a sentence whose ledger fact was filed under a segment (often via the
            # heading before it, "Turning to our Embedded segment.") belongs to that segment even if it never says so.
            for fid in fact_ids:
                node = registry.node_for_fact_segment(fact_segment.get(fid))
                if node and node != TOTAL and node not in seg_nodes:
                    seg_nodes.append(node)
                    if "segment_from_fact" not in flags:
                        flags.append("segment_from_fact")
            if any(fid in headline_guides for fid in fact_ids):
                cues["guidance_figure"] = ["ledger"]
            if fact_ids and "momentum" in topics and direction != "neutral":
                topics.remove("momentum")           # a sentence carrying a ledger figure is reporting performance
                topics.insert(0, "momentum")

            sig = Signal(
                topics=topics, horizon=horizon, direction=direction, stance=stance,
                withheld=bool(withheld_c), revision=revision, segments=seg_nodes,
                home=_home(seg_nodes, registry),
                fact_ids=fact_ids,
                guided=_guided(text, seg_nodes, registry) if horizon == "forward" else None,
                cues=cues, speaker=turn.speaker, role=turn.role, section=turn.section,
                turn_index=turn.index, sentence_index=sent.index, sentence=text,
                ev_start=sent.start, ev_end=sent.end, flags=flags,
            )
            # Every usable ledger figure in the sentence is a company total and the sentence lists three or more
            # segments: it is about the company, and the list just names what drove it ("...driven by significantly
            # higher sales of EPYC, Instinct, Ryzen and embedded processors"). One or two mentions still file it
            # under that segment (a company-level signings figure is still a Consulting statement).
            usable_facts = [fid for fid in fact_ids if fid in fact_segment]
            listed = [n for n in seg_nodes if registry.get(n).dimension == "segment"]
            if usable_facts and len(listed) >= 3 and all(fact_segment[fid] == TOTAL for fid in usable_facts):
                sig.home = None
                sig.flags.append("company_total_figure")

            conf = "high"
            if turn.section == "qa":
                conf = "medium"
            if turn.role == ROLE_UNKNOWN:
                conf = "low"
                sig.flags.append("speaker_role_unknown")
            if "segment_from_context" in flags:
                conf = "medium" if conf == "high" else conf
            if _PRONOUN_LEAD.search(text) and not sig.segments:
                sig.flags.append("context_dependent")
                conf = "medium" if conf == "high" else conf
            sig.confidence = conf
            sig.score = _score(sig, text)
            out.append(sig)

    _add_salience(out)
    for i, sig in enumerate(out, 1):
        sig.id = f"{stem}-S{i:03d}"
        problems = verify_signal(sig, transcript)
        sig.verified = not problems
        sig.flags.extend(problems)
    return [asdict(s) for s in out]


def verify_signal(sig: Signal, transcript: Transcript) -> list[str]:
    """Mechanical checks that the signal is what it claims to be: real text, at the stated place, said by management."""
    problems = []
    if transcript.text[sig.ev_start:sig.ev_end] != sig.sentence:
        problems.append("evidence_offsets_do_not_match_text")
    if not (0 <= sig.turn_index < len(transcript.turns)):
        problems.append("turn_index_out_of_range")
    else:
        turn = transcript.turns[sig.turn_index]
        if turn.role not in (ROLE_MANAGEMENT, ROLE_UNKNOWN):
            problems.append("not_management_speech")
        if turn.speaker != sig.speaker:
            problems.append("speaker_mismatch")
        if not (turn.start <= sig.ev_start and sig.ev_end <= turn.end):
            problems.append("evidence_outside_turn")
    return problems

"""
transcript.py
Turns a raw transcript (plain text with "Speaker:" labels, or Alpha Vantage's
structured turns) into a Transcript: ordered speaker turns with a role
(management / analyst / operator / ir / unknown), a section (prepared / qa),
and sentence offsets into one canonical cleaned text.

Every offset in the pipeline points into Transcript.text, so any fact can be
checked against the source by slicing that string.
"""

import re
from dataclasses import dataclass, field

from parser import clean_text, split_sentences_with_offsets

ROLE_MANAGEMENT = "management"
ROLE_ANALYST = "analyst"
ROLE_OPERATOR = "operator"
ROLE_IR = "ir"
ROLE_UNKNOWN = "unknown"


@dataclass
class Sentence:
    index: int
    start: int
    end: int
    text: str


@dataclass
class Turn:
    index: int
    speaker: str
    role: str
    section: str
    start: int
    end: int
    title: str | None = None
    sentiment: float | None = None
    sentences: list[Sentence] = field(default_factory=list)


@dataclass
class Transcript:
    filename: str
    text: str
    turns: list[Turn]
    meta: dict


# --------------------------------------------------------------------------- #
# Filename metadata
# --------------------------------------------------------------------------- #

_FILENAME_PATTERNS = [
    re.compile(r"^(?P<ticker>[A-Za-z][A-Za-z.\-]*)_Q(?P<q>[1-4])_(?P<y>\d{4})"),
    re.compile(r"^(?P<ticker>[A-Za-z][A-Za-z.\-]*)_(?P<y>\d{4})Q(?P<q>[1-4])"),
]


def parse_filename(name: str) -> dict:
    stem = name.rsplit(".", 1)[0]
    for pat in _FILENAME_PATTERNS:
        m = pat.match(stem)
        if m:
            return {"ticker": m["ticker"].upper(), "fiscal_quarter": int(m["q"]),
                    "fiscal_year": int(m["y"])}
    return {"ticker": None, "fiscal_quarter": None, "fiscal_year": None}


# --------------------------------------------------------------------------- #
# Speaker labels (plain-text transcripts)
# --------------------------------------------------------------------------- #

_NAME_TOKEN = r"[A-Z][A-Za-z.'\-]*"
_LABEL = re.compile(
    rf"^(?P<spk>{_NAME_TOKEN}(?:[ ]{_NAME_TOKEN}){{0,4}})(?:[ ]*\([^)]{{1,40}}\))?:[ \t]*(?P<rest>.*)$"
)
_NOT_SPEAKERS = {"Note", "First", "Second", "Third", "Finally", "Importantly", "Also",
                 "Question", "Answer", "Q", "A", "Source", "Sources", "Disclaimer"}

_QA_INTRO = re.compile(
    r"(?i)\b(?:first|next|final|last)\s+question\b|\bquestion\s+(?:comes|is)\s+from\b|\bline\s+of\b"
)
_ANALYST_INTRO = re.compile(
    r"(?:(?:question|line)\s+(?:comes\s+|is\s+)?(?:from|of)|(?:go|turn|move)\s+(?:now\s+)?to)\s+(?:the\s+line\s+of\s+)?"
    r"(?P<name>[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){1,3})"
    r"(?:\s+(?:from|with|of|at|representing)\s+"
    r"(?P<firm>[A-Z][\w&\-]*(?:\s+(?:of\s+|and\s+|&\s+)?[A-Z][\w&\-]*){0,4}))?"
)


def _last_name(name: str) -> str:
    return name.split()[-1].lower()


def _split_labelled_turns(text: str) -> list[tuple[str, int, int]]:
    """[(speaker, body_start, body_end)] for 'Name: body' transcripts."""
    raw: list[tuple[str, int]] = []
    pos = 0
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        m = _LABEL.match(line)
        if m and m["spk"] not in _NOT_SPEAKERS:
            raw.append((m["spk"], line_start + m.start("rest"), line_start))
    turns = []
    for i, (spk, body_start, _label_line_start) in enumerate(raw):
        end = raw[i + 1][2] if i + 1 < len(raw) else len(text)
        body = text[body_start:end]
        turns.append((spk, body_start, body_start + len(body.rstrip())))
    return turns


def _classify_text_roles(text: str, spans: list[tuple[str, int, int]]) -> tuple[list[str], dict]:
    """Infer a role per turn when the source has no title field."""
    speakers = [s for s, _, _ in spans]
    bodies = [text[a:b] for _, a, b in spans]

    ir = {s for s, body in zip(speakers, bodies)
          if re.search(r"(?i)investor relations", body[:800])}
    operators = {s for s in speakers if s.lower() in {"operator", "moderator", "conference operator"}}

    analysts: dict[str, str | None] = {}
    for s, body in zip(speakers, bodies):
        if s in operators:
            for m in _ANALYST_INTRO.finditer(body):
                analysts[_last_name(m["name"])] = m["firm"]

    qa_idx = None
    for i, (s, body) in enumerate(zip(speakers, bodies)):
        if (s in operators or s in ir) and _QA_INTRO.search(body):
            qa_idx = i
            break
    if qa_idx is None:
        for i, s in enumerate(speakers):
            if _last_name(s) in analysts:
                qa_idx = i
                break

    prepared_speakers = {s for i, s in enumerate(speakers)
                         if qa_idx is None or i < qa_idx}
    intro_text = " ".join(
        body for i, (s, body) in enumerate(zip(speakers, bodies))
        if (s in ir or s in operators) and (qa_idx is None or i < qa_idx))

    roles = []
    for s in speakers:
        if s in operators:
            roles.append(ROLE_OPERATOR)
        elif s in ir:
            roles.append(ROLE_IR)
        elif _last_name(s) in analysts:
            roles.append(ROLE_ANALYST)
        elif s in prepared_speakers or s in intro_text:
            roles.append(ROLE_MANAGEMENT)
        else:
            roles.append(ROLE_UNKNOWN)
    return roles, {"analyst_firms": {k: v for k, v in analysts.items()}, "qa_start_turn": qa_idx}


def _role_from_title(title: str | None) -> str:
    t = (title or "").strip().lower()
    if not t:
        return ROLE_UNKNOWN
    if t == "operator":
        return ROLE_OPERATOR
    if "analyst" in t:
        return ROLE_ANALYST
    if "investor relations" in t or re.search(r"\bir\b", t):        # "VP, Financial Strategy and IR"
        return ROLE_IR
    return ROLE_MANAGEMENT


def _finish(filename: str, text: str, turn_specs: list[dict], meta: dict) -> Transcript:
    """turn_specs: dicts with speaker, role, start, end, title, sentiment (already ordered)."""
    qa_idx = meta.get("qa_start_turn")
    if qa_idx is None:
        for i, t in enumerate(turn_specs):
            if t["role"] in (ROLE_OPERATOR, ROLE_IR) and _QA_INTRO.search(text[t["start"]:t["end"]]):
                qa_idx = i
                break
    if qa_idx is None:
        for i, t in enumerate(turn_specs):
            if t["role"] == ROLE_ANALYST:
                qa_idx = i
                break

    turns: list[Turn] = []
    for i, t in enumerate(turn_specs):
        section = "qa" if qa_idx is not None and i >= qa_idx else "prepared"
        turn = Turn(index=i, speaker=t["speaker"], role=t["role"], section=section,
                    start=t["start"], end=t["end"], title=t.get("title"),
                    sentiment=t.get("sentiment"))
        for j, (s, e, txt) in enumerate(
                split_sentences_with_offsets(text[t["start"]:t["end"]], base=t["start"])):
            turn.sentences.append(Sentence(index=j, start=s, end=e, text=txt))
        turns.append(turn)

    meta = dict(meta)
    meta["qa_start_turn"] = qa_idx
    meta["qa_boundary_found"] = qa_idx is not None
    meta["has_speaker_labels"] = bool(turn_specs) and turn_specs[0]["speaker"] != "Unknown"
    roles_seen = {}
    for t in turns:
        roles_seen.setdefault(t.speaker, t.role)
    meta["speakers"] = [{"name": n, "role": r} for n, r in roles_seen.items()]
    return Transcript(filename=filename, text=text, turns=turns, meta=meta)


# --------------------------------------------------------------------------- #
# Public builders
# --------------------------------------------------------------------------- #

def parse_transcript(raw_text: str, filename: str) -> Transcript:
    """Plain-text transcript with 'Speaker: text' labels."""
    text = clean_text(raw_text)
    meta = {"source": "text_file", **parse_filename(filename)}
    spans = _split_labelled_turns(text)
    if not spans:
        specs = [{"speaker": "Unknown", "role": ROLE_UNKNOWN, "start": 0, "end": len(text)}]
        meta["qa_start_turn"] = None
        return _finish(filename, text, specs, meta)

    roles, extra = _classify_text_roles(text, spans)
    meta.update(extra)
    specs = [{"speaker": s, "role": r, "start": a, "end": b}
             for (s, a, b), r in zip(spans, roles)]
    return _finish(filename, text, specs, meta)


def build_from_structured(turns_raw: list[dict], filename: str, source_meta: dict | None = None) -> Transcript:
    """
    Structured turns as returned by Alpha Vantage:
    [{"speaker", "title", "content", "sentiment"}, ...]. Roles come from `title`.
    The canonical text is rebuilt as 'Speaker: content' blocks so offsets stay meaningful.
    """
    parts, specs = [], []
    pos = 0
    for t in turns_raw:
        speaker = clean_text(str(t.get("speaker", "") or "")) or "Unknown"
        content = clean_text(str(t.get("content", "") or ""))
        label = f"{speaker}: "
        specs.append({
            "speaker": speaker,
            "role": _role_from_title(t.get("title")),
            "title": t.get("title"),
            "sentiment": _to_float(t.get("sentiment")),
            "start": pos + len(label),
            "end": pos + len(label) + len(content),
        })
        block = label + content
        parts.append(block)
        pos += len(block) + 2
    text = "\n\n".join(parts)

    meta = {"source": "alphavantage", **parse_filename(filename), **(source_meta or {})}
    meta["qa_start_turn"] = None
    return _finish(filename, text, specs, meta)


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

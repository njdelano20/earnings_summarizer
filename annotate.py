"""
annotate.py
A readable page for one earnings call, in two states, so the effect of checking is visible.

  UNCHECKED  the whole transcript, generated automatically. Every figure the extractor captured is highlighted with the
             fact it became (metric / segment / value / confidence); every figure it could not attach to a metric is
             underlined. Nothing has been checked: a highlight means "captured", never "correct".
  CHECKED    the same page marked against the hand-written answer key (gold/<call>.json):
               green tick   the answer key lists this fact
               red cross    the answer key forbids it (struck through, with the note if the key gives one)
               grey dot     captured, but the answer key says nothing about it
               blue plus    the answer key expects a fact the extractor MISSED (placed at its `where` phrase if the
                            gold entry has one, otherwise listed in the changes table at the top)

    py annotate.py CPRT_2026Q3                 output/review/CPRT_2026Q3_unchecked.html
    py annotate.py CPRT_2026Q3 --check         output/review/CPRT_2026Q3_checked.html   (needs gold/CPRT_2026Q3.json)
    py annotate.py AMD_2026Q2 --check --facts-json old.json --label BEFORE_fixes
                                               mark facts from a saved extraction (an older version of the code) instead
    add --open to show the page in the browser. batch.py writes the UNCHECKED page for every call it processes.

Gold entries may carry two extra optional keys that the scorer ignores: "where" (a phrase from the transcript that
locates the fact) and "note" (why a forbidden fact is wrong, or what the right reading is).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import periods
from batch import _amount, fact_value_text
from evaluate import load_transcript, matches

ROOT = Path(__file__).resolve().parent
GOLD = ROOT / "gold"
REVIEW = ROOT / "output" / "review"

_CSS = """
:root{--bg:#fff;--fg:#1d2330;--mut:#6b7280;--line:#e5e7eb;--card:#f8fafc;--hi:#fff3bf;--unc:#9ca3af;--ok:#15803d;--bad:#b91c1c;
--add:#1d4ed8;--nog:#6b7280;--okbg:#dcfce7;--badbg:#fee2e2;--addbg:#dbeafe;--nogbg:#eef0f3}
@media (prefers-color-scheme:dark){:root{--bg:#12151c;--fg:#e6e8ee;--mut:#9aa3b2;--line:#2a2f3a;--card:#1a1f29;--hi:#5b4b00;
--unc:#7b8494;--ok:#4ade80;--bad:#f87171;--add:#60a5fa;--nog:#9aa3b2;--okbg:#12351f;--badbg:#3f1a1a;--addbg:#172a4d;--nogbg:#232935}}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,Segoe UI,sans-serif;margin:0;padding:16px;max-width:980px;margin-inline:auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}
.banner{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:10px 14px;margin:10px 0}
.banner.unchecked{border-color:#d97706}.banner.checked{border-color:var(--ok)}
.legend span,.chip{display:inline-block;border-radius:4px;padding:0 6px;margin:0 2px;font-size:12px;white-space:nowrap}
mark{background:var(--hi);color:inherit;padding:0 2px;border-radius:3px}
mark.chg{background:transparent;border-bottom:2px solid var(--hi)}
mark.unc{background:transparent;border-bottom:1px dotted var(--unc)}
mark.wrong{text-decoration:line-through;text-decoration-color:var(--bad);text-decoration-thickness:2px}
.chip.ok{background:var(--okbg);color:var(--ok)}.chip.wrong{background:var(--badbg);color:var(--bad)}
.chip.unlisted{background:var(--nogbg);color:var(--nog)}.chip.plain{background:var(--nogbg);color:var(--mut)}
.chip.miss{background:var(--addbg);color:var(--add)}
.turn{border-top:1px solid var(--line);padding:8px 0}.who{font-weight:600}.role{color:var(--mut);font-size:12px;margin-left:6px}
.turn.analyst{opacity:.72}.turn.operator,.turn.ir{opacity:.6}
.s.bad{border-left:3px solid var(--bad);padding-left:6px;margin-left:-9px}
.s.add{border-left:3px solid var(--add);padding-left:6px;margin-left:-9px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
"""


def esc(text: str) -> str:
    return html.escape(text, quote=True)


# --------------------------------------------------------------------------- #
# checking facts against the answer key
# --------------------------------------------------------------------------- #

def check_against_gold(facts: list[dict], gold: dict) -> tuple[dict, list[dict]]:
    """({fact id: (status, note)}, missed gold entries). status = ok | wrong | unlisted."""
    expected, forbidden = gold.get("expected", []), gold.get("forbidden", [])
    status = {}
    for f in facts:
        bad = next((w for w in forbidden if matches(f, w)), None)
        if bad:
            status[f["id"]] = ("wrong", bad.get("note", ""))
        elif any(matches(f, w) for w in expected):
            status[f["id"]] = ("ok", "")
        else:
            status[f["id"]] = ("unlisted", "")
    missed = [w for w in expected if not any(matches(f, w) for f in facts)]
    return status, missed


def gold_entry_text(w: dict) -> str:
    """A gold entry in words: 'reported revenue / data center / level = $6.7B [+107% yoy]'."""
    unit = w.get("unit", "")
    value = ""
    if "value" in w:
        value = _amount(w["value"], unit)
        if "value_high" in w:
            value += f" to {_amount(w['value_high'], unit)}"
    elif "descriptor_contains" in w:
        value = f'"{w["descriptor_contains"]}"'
    change = ""
    if "change" in w:
        c = w["change"]
        change = f" [{_amount(c['value'], c.get('unit', ''))}{'/' + c['basis'] if c.get('basis') else ''}]"
    bits = [str(w[k]) for k in ("kind", "period", "accounting", "basis") if k in w]
    head = " ".join(bits + [f"{w.get('metric', '?')} / {w.get('segment', 'any segment')} / {w.get('stat', '')}".rstrip(" /")])
    return f"{head} = {value}{change}".strip()


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _claim(text: str, needle: str, used: list[tuple[int, int]]) -> tuple[int, int] | None:
    """First occurrence of `needle` in `text` that overlaps nothing already marked."""
    start = 0
    while needle:
        i = text.find(needle, start)
        if i < 0:
            return None
        span = (i, i + len(needle))
        if all(span[1] <= a or span[0] >= b for a, b in used):
            return span
        start = i + 1
    return None


def _sentence_html(sent, facts: list[dict], unclaimed: list[dict], status: dict, checked: bool,
                   missed_here: list[dict]) -> str:
    text = sent.text
    used: list[tuple[int, int]] = []
    marks = []                                                     # (start, end, css class, chip html, tooltip)
    for f in facts:
        span = _claim(text, f["figure_text"] or "", used)
        if not span:
            continue
        used.append(span)
        st, note = status.get(f["id"], ("plain", ""))
        glyph = {"ok": "&#10003; ", "wrong": "&#10007; ", "unlisted": "&middot; "}.get(st, "") if checked else ""
        label = f"{f['metric']} / {f['segment'] or '?'} / {f['stat']} = {fact_value_text(f)}"
        tip = f"{f['kind']} {label} | {f['confidence']} confidence | period {f['period'] or '?'}"
        if f["flags"]:
            tip += " | " + ", ".join(f["flags"])
        if note:
            tip += f" | CHECK: {note}"
        chip = (f'<span class="chip {st if checked else "plain"}" title="{esc(tip)}">{glyph}{esc(label)} '
                f'&middot; {esc(f["confidence"])}</span>')
        if note:
            chip += f'<span class="chip wrong">{esc(note)}</span>'
        marks.append((span[0], span[1], "wrong" if (checked and st == "wrong") else "", chip, tip))
        for c in f.get("changes", []):
            cs = _claim(text, c["text"], used)
            if cs:
                used.append(cs)
                marks.append((cs[0], cs[1], "chg", "", f"change attached to: {label}"))
    for u in unclaimed:
        span = _claim(text, u["figure_text"], used)
        if span:
            used.append(span)
            marks.append((span[0], span[1], "unc", "", f"figure not attached to any metric ({u['reason']})"))
    marks.sort(key=lambda m: m[0])
    out, pos = [], 0
    for start, end, cls, chip, tip in marks:
        out.append(esc(text[pos:start]))
        out.append(f'<mark class="{cls or ""}" title="{esc(tip)}">{esc(text[start:end])}</mark>{chip}')
        pos = end
    out.append(esc(text[pos:]))
    for w in missed_here:
        out.append(f' <span class="chip miss" title="the answer key expects this; the extractor did not produce it">'
                   f'+ MISSED: {esc(gold_entry_text(w))}</span>')
    css = "s"
    if any(status.get(f["id"], ("",))[0] == "wrong" for f in facts) and checked:
        css += " bad"
    if missed_here:
        css += " add"
    return f'<span class="{css}">{"".join(out)}</span> '


def render_page(transcript, result: dict, gold: dict | None = None, label: str = "") -> str:
    checked = gold is not None
    facts = result["facts"]
    status, missed = check_against_gold(facts, gold) if checked else ({}, [])
    by_sentence: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for f in facts:
        by_sentence[(f["turn_index"], f["sentence_index"])].append(f)
    unc_by: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for u in result["unclaimed"]:
        unc_by[(u["turn_index"], u["ev_start"])].append(u)

    # where each missed gold entry goes: the sentence containing its `where` phrase
    placed: dict[tuple[int, int], list[dict]] = defaultdict(list)
    unplaced = []
    for w in missed:
        loc = None
        phrase = (w.get("where") or "").lower()
        if phrase:
            for turn in transcript.turns:
                for sent in turn.sentences:
                    if phrase in sent.text.lower():
                        loc = (turn.index, sent.index)
                        break
                if loc:
                    break
        (placed[loc] if loc else unplaced).append(w)

    meta = transcript.meta
    title = " ".join(str(x) for x in (meta.get("ticker"), f"Q{meta['fiscal_quarter']}" if meta.get("fiscal_quarter") else None,
                                      meta.get("fiscal_year")) if x) or transcript.filename
    title += periods.title_suffix(meta)
    n_ok = sum(1 for s, _ in status.values() if s == "ok")
    n_bad = sum(1 for s, _ in status.values() if s == "wrong")
    n_unl = sum(1 for s, _ in status.values() if s == "unlisted")
    n_exp = len((gold or {}).get("expected", []))
    if checked:
        banner = (f'<div class="banner checked"><b>CHECKED{" (" + esc(label) + ")" if label else ""}</b> against the answer key '
                  f'gold/{esc(Path(transcript.filename).stem)}.json, written by reading the transcript. '
                  f'The key expects {n_exp} facts: <b>{n_exp - len(missed)}</b> found, <b>{len(missed)}</b> missed. '
                  f'Of the {len(facts)} facts extracted: <b>{n_ok}</b> confirmed, <b>{n_bad}</b> wrong (forbidden), '
                  f'<b>{n_unl}</b> not covered by the key (the key is partial, so these are unreviewed, not wrong).</div>')
    else:
        banner = ('<div class="banner unchecked"><b>UNCHECKED.</b> Generated automatically from the transcript. A highlighted '
                  'figure means the extractor captured it, not that it is right; nothing here has been reviewed.</div>')
    legend = ('<p class="legend"><span><mark>figure</mark> captured as a fact</span><span><mark class="chg">change</mark> '
              'attached to a fact</span><span><mark class="unc">figure</mark> not attached to any metric</span>'
              + ('<span class="chip ok">&#10003; in the key</span><span class="chip wrong">&#10007; forbidden</span>'
                 '<span class="chip unlisted">&middot; not in the key</span><span class="chip miss">+ missed</span>' if checked else '')
              + '</p><p style="color:var(--mut);font-size:13px">Analyst questions are shown faded: only management speech is mined.'
                ' Hover a highlight for the fact\'s details.</p>')

    changes = ""
    if checked and (n_bad or missed):
        rows = []
        for f in facts:
            if status[f["id"]][0] == "wrong":
                rows.append(f'<tr><td><span class="chip wrong">&#10007; remove</span></td><td>{esc(f["metric"])} / '
                            f'{esc(f["segment"] or "?")} = {esc(fact_value_text(f))}</td><td>{esc(status[f["id"]][1] or "forbidden by the key")}'
                            f'<br><i>{esc(f["sentence"][:160])}</i></td></tr>')
        for w in missed:
            rows.append(f'<tr><td><span class="chip miss">+ add</span></td><td>{esc(gold_entry_text(w))}</td>'
                        f'<td>{esc(w.get("note", "") or ("near: " + w["where"] if w.get("where") else "expected by the key"))}</td></tr>')
        changes = ('<h2>Changes the check makes</h2><table><tr><th></th><th>Fact</th><th>Why / where</th></tr>'
                   + "".join(rows) + '</table>')

    body = []
    for turn in transcript.turns:
        sents = []
        for sent in turn.sentences:
            key = (turn.index, sent.index)
            sents.append(_sentence_html(sent, by_sentence.get(key, []), unc_by.get((turn.index, sent.start), []), status,
                                        checked, placed.get(key, [])))
        body.append(f'<div class="turn {esc(turn.role)}"><div><span class="who">{esc(turn.speaker)}</span>'
                    f'<span class="role">{esc(turn.role)} &middot; {esc(turn.section)}</span></div><div>{"".join(sents)}</div></div>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} {"checked" if checked else "unchecked"}</title><style>{_CSS}</style></head><body>'
            f'<h1>{esc(title)}</h1><div style="color:var(--mut);font-size:13px">{esc(transcript.filename)} &middot; '
            f'generated {date.today().isoformat()}</div>{banner}{legend}{changes}<h2>Transcript</h2>{"".join(body)}</body></html>')


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #

def write_page(transcript, result: dict, out_dir: Path, gold: dict | None = None, label: str = "") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(transcript.filename).stem
    suffix = ("checked" if gold is not None else "unchecked") + (f"_{label}" if label else "")
    path = out_dir / f"{stem}_{suffix}.html"
    path.write_text(render_page(transcript, result, gold, label), encoding="utf-8")
    return path


def find_call(stem: str) -> Path:
    for path in (ROOT / "data" / "raw" / "alphavantage" / f"{stem}.json", ROOT / "data" / "transcripts" / f"{stem}.txt"):
        if path.exists():
            return path
    raise FileNotFoundError(f"no cached transcript named {stem} (looked in data/raw/alphavantage and data/transcripts)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Readable annotated transcript, unchecked or checked against the answer key")
    ap.add_argument("call", help="cache name without extension, e.g. CPRT_2026Q3")
    ap.add_argument("--check", action="store_true", help="mark the page against gold/<call>.json")
    ap.add_argument("--facts-json", help="use a saved extraction (a *_facts.json or older-code result) instead of running the extractor")
    ap.add_argument("--label", default="", help="added to the file name and banner, e.g. BEFORE_fixes")
    ap.add_argument("--out", default=str(REVIEW))
    ap.add_argument("--open", action="store_true", help="open the page in the browser")
    args = ap.parse_args()

    from facts import extract_facts
    transcript = load_transcript(find_call(args.call))
    periods.attach_period(transcript.meta)
    result = json.loads(Path(args.facts_json).read_text(encoding="utf-8")) if args.facts_json else extract_facts(transcript)
    gold = None
    if args.check:
        gold_path = GOLD / f"{args.call}.json"
        if not gold_path.exists():
            print(f"No answer key at {gold_path}. Write the gold first; the unchecked page needs none.", file=sys.stderr)
            return 1
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
    path = write_page(transcript, result, Path(args.out), gold, args.label)
    print(path)
    if args.open and hasattr(os, "startfile"):
        os.startfile(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

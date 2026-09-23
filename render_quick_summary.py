"""
render_quick_summary.py
Stage 2 of the "generalize the quick-summary template to any company" pipeline: takes
export_quick_summary.py's JSON payload (output/quick_summary/<STEM>.json) and produces the
SAME page design as docs/sample_AAPL_Q3_2026.md/.html -- the two-column matplotlib-chart layout
is the real target template (confirmed directly by the user 2026-09-23), not a redesign.

Generates all 8 sections, same as the AAPL reference doc: the 5 data-driven ones (headline
scoreboard, business segments -- now with per-segment driver bullets too, capital allocation,
balance sheet, insider activity) plus the 3 narrative ones (competitive environment, product
development, macro/regulatory), via narrative.py -- real transcript sentences already
topic-tagged and evidence-verified by signals.py (cached in output/<STEM>_snapshot.json), never
LLM-paraphrased or synthesized. A symbol with no cached snapshot, or no real evidence for a given
section, simply doesn't get that section -- never padded with placeholder text to fake
completeness. Plus a real Source documents section (the actual cached transcript + the real
filed SEC document, both live-verified, not a generic search link).

    py render_quick_summary.py AMD_2026Q2          regenerate charts + docs/sample_AMD_2026Q2.md/.html
    py render_quick_summary.py --all               every symbol-quarter already exported
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import charts
import narrative
import render_sample
import sec_edgar

ROOT = Path(__file__).resolve().parent
QUICK_SUMMARY_DIR = ROOT / "output" / "quick_summary"
DOCS = ROOT / "docs"
TRANSCRIPTS_DIR = ROOT / "data" / "transcripts"

_CONNECTORS = {"and", "of", "the", "for", "in"}          # stay lowercase mid-name
_ALL_CAPS_SUFFIXES = {"llc", "lp", "plc"}                 # abbreviations, not title-cased words


def _company_name(symbol: str) -> str:
    """SEC's own entityName (already cached locally, no DB dependency) -- title-cased for
    display since SEC's is often all-caps ('ADVANCED MICRO DEVICES INC'). Entity suffixes like
    Inc/Corp go through the normal capitalize() branch (that already gives 'Inc'/'Corp'
    correctly); only true grammatical connectors get force-lowercased, and LLC/LP/PLC-style
    abbreviations get force-uppercased -- conflating "Inc" with "and" was a real bug (both were
    in one lowercase set) caught by looking at real output, not by reasoning about the code."""
    try:
        facts = sec_edgar.fetch_company_facts(symbol)
        raw = facts.get("entityName") or symbol
    except sec_edgar.SECEdgarError:
        return symbol
    words = raw.split(" ")
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _CONNECTORS and i > 0:
            out.append(lw)
        elif lw.strip(".,") in _ALL_CAPS_SUFFIXES:
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out).replace(",", "")


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}${v/1e9:.2f}B" if v >= 1e9 else f"{sign}${v/1e6:.1f}M" if v >= 1e6 else f"{sign}${v:.0f}"


def _real_filing_url(symbol: str, period_end_guess: str) -> tuple[str, str, str] | None:
    """(url, form, filed_date) for the real filed 10-Q/10-K nearest period_end_guess, or None.
    Same construction proven against AAPL: https://www.sec.gov/Archives/edgar/data/<cik>/
    <accession-no-dashes>/<primaryDocument> -- the actual filed document, not a search link."""
    from datetime import date
    try:
        cik = sec_edgar.cik_for(symbol)
        subs = sec_edgar.fetch_submissions(symbol)
    except sec_edgar.SECEdgarError:
        return None
    if not cik:
        return None
    recent = subs.get("filings", {}).get("recent", {})
    target = date.fromisoformat(period_end_guess)
    best, best_gap = None, None
    for form, rd, accn, doc, filed in zip(
        recent.get("form", []), recent.get("reportDate", []), recent.get("accessionNumber", []),
        recent.get("primaryDocument", []), recent.get("filingDate", [])):
        if form not in ("10-Q", "10-K") or not rd:
            continue
        try:
            gap = abs((date.fromisoformat(rd) - target).days)
        except ValueError:
            continue
        if gap <= 45 and (best_gap is None or gap < best_gap):
            best, best_gap = (form, rd, accn, doc, filed), gap
    if not best:
        return None
    form, rd, accn, doc, filed = best
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}"
    return url, rd, filed


def _headline_scoreboard_section(payload, stem, name, narr) -> list[str] | None:
    if not payload.get("scoreboard"):
        return None
    return [f"![Headline scoreboard]({stem}_scoreboard.png)", ""]


def _business_segments_section(payload, stem, name, narr) -> list[str] | None:
    segs = payload.get("segments") or []
    if not segs:
        return None
    lines = [f"![Revenue by segment]({stem}_segments.png)", ""]
    lines += ["| Segment | Revenue | Growth (yoy) |", "|---|---|---|"]
    for s in segs:
        growth = f"{s['yoy_pct']:+.0f}%" if s.get("yoy_pct") is not None else "—"
        lines.append(f"| {s['name']} | {_fmt_usd(s['revenue'])} | {growth} |")
    lines += ["", f"{name} doesn't disclose gross margin by individual segment in this data, "
                  f"so it's left out rather than estimated.", ""]
    drivers = narr.get("segment_drivers") or {}
    if drivers:
        lines.append("**What's driving each segment:**")
        lines.append("")
        for seg_name, sentences in drivers.items():
            lines.append(f"- **{seg_name}**: {' '.join(sentences)}")
        lines.append("")
    return lines


def _capital_allocation_section(payload, stem, name, narr) -> list[str] | None:
    cap = payload.get("capital_allocation") or {}
    if not cap:
        return None
    lines = [f"![Capital allocation]({stem}_capital.png)", ""]
    lines += ["| Use of cash | Amount |", "|---|---|"]
    for label, v in cap.items():
        lines.append(f"| {label} | {_fmt_usd(v)} |")
    shareholder = (cap.get("Dividends paid") or 0) + (cap.get("Share repurchases") or 0)
    if cap.get("Dividends paid") is not None or cap.get("Share repurchases") is not None:
        lines += ["", f"Total returned to shareholders (dividends + buybacks): **{_fmt_usd(shareholder)}**."]
    lines.append("")   # unconditional -- a table with no trailing blank line runs into the next heading
    return lines


def _balance_sheet_section(payload, stem, name, narr) -> list[str] | None:
    bs = payload.get("balance_sheet") or {}
    if bs.get("cash") is None and bs.get("debt") is None:
        return None
    lines = []
    # the cash/debt bar chart needs BOTH figures (render_one's balance_sheet_chart call has the
    # same guard) -- a symbol with only one of the two gets a table, not a broken image
    if bs.get("cash") is not None and bs.get("debt") is not None:
        lines += [f"![Balance sheet snapshot]({stem}_balance.png)", ""]
    lines += ["| | This quarter |", "|---|---|"]
    if bs.get("cash") is not None:
        lines.append(f"| Cash & securities | {_fmt_usd(bs['cash'])} |")
    if bs.get("debt") is not None:
        lines.append(f"| Total debt | {_fmt_usd(bs['debt'])} |")
    if bs.get("net_cash") is not None:
        label = "Net cash" if bs["net_cash"] >= 0 else "Net debt"
        lines.append(f"| {label} | {_fmt_usd(bs['net_cash'])} |")
    lines.append("")
    ladder = bs.get("debt_maturity")
    if ladder:
        parts = ", ".join(f"{_fmt_usd(v)} in {k.lower()}" for k, v in ladder.items())
        as_of = bs.get("debt_maturity_as_of")
        lines += [f"**When it's due**: {parts} — the aggregate principal-by-year figure "
                 f"{name} files with the SEC" + (f" (as of {as_of})" if as_of else "") +
                 f"; individual notes (coupon, specific maturity date) aren't broken out "
                 f"at that level in this data.", ""]
    return lines


def _insider_activity_section(payload, stem, name, narr) -> list[str] | None:
    insider = payload.get("insider_activity")
    if not insider or not insider.get("by_executive"):
        return None
    lines = [f"**{insider.get('window_start', '')} to {insider.get('window_end', '')}**: "
            f"{_fmt_usd(insider.get('total_value'))} in real open-market insider stock sales.", ""]
    lines += ["| Insider | Title | Shares sold | Value |", "|---|---|---|---|"]
    for e in insider["by_executive"]:
        shares = f"{e['shares']:,.0f}" if e.get("shares") is not None else "—"
        lines.append(f"| {e['name'].title()} | {e.get('title') or '—'} | {shares} | {_fmt_usd(e.get('value'))} |")
    lines.append("")
    return lines


def _competitive_environment_section(payload, stem, name, narr) -> list[str] | None:
    sentences = narr.get("competitive_environment")
    if not sentences:
        return None
    return [" ".join(sentences), ""]


def _product_development_section(payload, stem, name, narr) -> list[str] | None:
    sentences = narr.get("product_development")
    if not sentences:
        return None
    return [f"- {s}" for s in sentences] + [""]


def _macro_regulatory_section(payload, stem, name, narr) -> list[str] | None:
    sentences = narr.get("macro_regulatory")
    if not sentences:
        return None
    return [f"- {s}" for s in sentences] + [""]


_SECTIONS = [
    ("Headline scoreboard", _headline_scoreboard_section),
    ("Business segments", _business_segments_section),
    ("Capital allocation", _capital_allocation_section),
    ("Balance sheet snapshot", _balance_sheet_section),
    ("Insider activity", _insider_activity_section),
    ("Competitive environment", _competitive_environment_section),
    ("Product development", _product_development_section),
    ("Macro / regulatory", _macro_regulatory_section),
]


def build_markdown(payload: dict, stem: str) -> str:
    symbol, quarter = payload["symbol"], payload["quarter"]
    name = _company_name(symbol)
    quarter_label = quarter.replace("_", " ")
    lines = [f"# {name} ({symbol}) — {quarter_label} — Quick Summary", ""]

    narr = narrative.build_narrative(stem, payload.get("segments") or [])

    # numbered sequentially over whichever sections actually have real content -- a company
    # missing one data point (e.g. IBM has no segment breakdown) gets 1, 2, 3... not 1, 3, 4
    # with a silent gap where "2" used to be
    n = 0
    for title, builder in _SECTIONS:
        body = builder(payload, stem, name, narr)
        if body is None:
            continue
        n += 1
        lines += [f"## {n}. {title}", ""] + body

    # Source documents -- real links only, never a generic search URL
    src_lines = []
    transcript_path = TRANSCRIPTS_DIR / f"{stem}.txt"
    if transcript_path.exists():
        dest = DOCS / f"{stem}_transcript.txt"
        shutil.copy(transcript_path, dest)
        src_lines.append(f"- [Full earnings call transcript]({stem}_transcript.txt)")
    filing = _real_filing_url(symbol, payload.get("period_end_guess", ""))
    if filing:
        url, rd, filed = filing
        src_lines.append(f"- [Filing with the SEC]({url}) (period ended {rd}, filed {filed})")
    if src_lines:
        lines += ["---", "", "## Source documents", ""] + src_lines

    return "\n".join(lines) + "\n"


_PROTECTED = {"AAPL_Q3_2026"}   # the original, hand-curated reference doc -- this generator's
# auto-generated version must never silently clobber it, even though it now produces all 8
# sections too. Regenerate AAPL's own doc only via a deliberate --force-aapl, never as a side
# effect of --all or a routine run.


def render_one(stem: str, force: bool = False) -> Path:
    if stem in _PROTECTED and not force:
        raise RuntimeError(f"{stem} is the hand-curated reference doc (docs/sample_{stem}.md) -- "
                           f"refusing to overwrite it with the auto-generated version. Pass "
                           f"force=True / --force-aapl if this is really intended.")
    payload = json.loads((QUICK_SUMMARY_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    symbol = payload["symbol"]

    if payload.get("scoreboard"):
        charts.headline_scoreboard_chart(
            payload["scoreboard"], f"{symbol} {payload['quarter'].replace('_', ' ')} -- Headline scoreboard",
            DOCS / f"{stem}_scoreboard.png")
    if payload.get("segments"):
        charts.segment_revenue_chart(
            [{"name": s["name"], "revenue_b": s["revenue"] / 1e9, "yoy_pct": s.get("yoy_pct") or 0}
             for s in payload["segments"]],
            f"{symbol} {payload['quarter'].replace('_', ' ')} -- Revenue by segment",
            DOCS / f"{stem}_segments.png")
    if payload.get("capital_allocation"):
        charts.capital_allocation_chart(
            {k: v / 1e9 for k, v in payload["capital_allocation"].items()},
            f"{symbol} {payload['quarter'].replace('_', ' ')} -- Capital allocation",
            DOCS / f"{stem}_capital.png")
    bs = payload.get("balance_sheet") or {}
    if bs.get("cash") is not None and bs.get("debt") is not None:
        ladder_b = {k: v / 1e9 for k, v in bs["debt_maturity"].items()} if bs.get("debt_maturity") else None
        charts.balance_sheet_chart(
            bs["cash"] / 1e9, bs["debt"] / 1e9, (bs.get("net_cash") or 0) / 1e9,
            f"{symbol} {payload['quarter'].replace('_', ' ')} -- Balance sheet snapshot",
            DOCS / f"{stem}_balance.png",
            maturity_b=ladder_b, maturity_as_of=bs.get("debt_maturity_as_of"))

    md = build_markdown(payload, stem)
    md_path = DOCS / f"sample_{stem}.md"
    md_path.write_text(md, encoding="utf-8")
    html_path = DOCS / f"sample_{stem}.html"
    html_path.write_text(render_sample.render(md_path), encoding="utf-8")
    return html_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stem", nargs="?", help="e.g. AMD_2026Q2 -- matches an output/quick_summary/*.json file")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force-aapl", action="store_true",
                    help="allow overwriting the hand-curated docs/sample_AAPL_Q3_2026.md (normally refused)")
    args = ap.parse_args()

    if args.all:
        stems = sorted(p.stem for p in QUICK_SUMMARY_DIR.glob("*.json"))
        if not args.force_aapl:
            stems = [s for s in stems if s not in _PROTECTED]
    elif args.stem:
        stems = [args.stem]
    else:
        ap.error("pass a stem or --all")
        return 1

    for stem in stems:
        try:
            out = render_one(stem, force=args.force_aapl)
            print(f"{stem}: wrote {out}")
        except Exception as exc:
            print(f"{stem}: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

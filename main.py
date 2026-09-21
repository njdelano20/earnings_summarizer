"""
main.py
Orchestrates the pipeline: fetch -> parse into speaker turns -> extract facts -> write.

Usage:
    py main.py                          process every local .txt transcript and every cached Alpha Vantage JSON
    py main.py --av IBM 2024Q1          fetch (and cache) a transcript from Alpha Vantage, then process it
    py main.py --av AAPL 2026q2 --av MSFT 2026q2
    py main.py --av IBM 2024Q1 --demo   use Alpha Vantage's public demo key (works for IBM 2024Q1 only)
    py main.py --av CPRT 2026Q2 --fiscal-label   the quarter is already the company's own fiscal label
    py main.py --audit                  also print every numeric figure that was NOT captured as a fact

Quarter labels are CALENDAR quarters on one Jan-Dec calendar: 2026q2 is every company's quarter that mostly happened
in April-June (its middle month falls there), reported during the following earnings season. periods.py turns it into
the company's own fiscal quarter, the label Alpha Vantage wants: Copart (year ends July) 2026q2 -> fiscal 2026 Q4 (May-Jul),
AMD 2026q2 -> Q2 2026. Cached files keep Alpha Vantage's own label (CPRT_2026Q4.json); reports state both. Check any
mapping with `py periods.py CPRT 2026q2`. Every run also compares the quarter the call names for itself with the
expected one. A label whose call is not out yet simply fails to fetch: try again later.

Alpha Vantage key: set the ALPHAVANTAGE_API_KEY environment variable (free key at alphavantage.co).
Outputs land in output/ as <name>_summary.md and <name>_facts.json (the fact ledger), plus
<name>_snapshot.md and <name>_snapshot.json (the business snapshot: momentum, product and competitive
developments, outlook and management's stance, broken out by segment).
"""

import argparse
import json
import sys
from pathlib import Path

import periods
from fetchers import AlphaVantageError, fetch_from_alphavantage, fetch_from_folder
from facts import extract_facts
from parser import clean_text
from snapshot import build_snapshot
from summarizer import summarize
from transcript import build_from_structured, parse_transcript
from writer import write_facts_json, write_snapshot, write_summary

ROOT = Path(__file__).resolve().parent
TRANSCRIPT_FOLDER = ROOT / "data" / "transcripts"
AV_CACHE_FOLDER = ROOT / "data" / "raw" / "alphavantage"
OUTPUT_FOLDER = ROOT / "output"


def _annotate_period(transcript) -> None:
    """Attach the calendar quarter / quarter-end to the transcript's meta (for report titles) and check that the call
    names the quarter we think it is. A mismatch means the fiscal calendar on file for the symbol is probably wrong."""
    cq = periods.attach_period(transcript.meta)
    if cq is None:
        return
    check = periods.check_call_period(transcript.text, cq)
    transcript.meta["period_check"] = check
    flag = {"pass": "ok", "warn": "WARNING", "unknown": "unchecked"}[check["status"]]
    print(f"  period [{flag}]: {cq.caption()}; {check['detail']}")
    if cq.calendar_label == cq.fiscal_label and cq.source == periods.ASSUMED:
        print(f"  note: no fiscal calendar on file for {cq.symbol}; assuming its year is the calendar year "
              f"(add it to fiscal_calendars.json if not)")


def _process(transcript, audit: bool) -> None:
    print(f"{transcript.filename}:")
    _annotate_period(transcript)
    result = extract_facts(transcript)
    summary = summarize(clean_text(transcript.text))
    facts_path = write_facts_json(transcript.filename, result, str(OUTPUT_FOLDER))
    md_path = write_summary(transcript.filename, summary, str(OUTPUT_FOLDER), facts_result=result)
    s = result["stats"]
    print(f"  call refers to its own period as "
          f"'{result['meta'].get('reported_period_label')}': {s['facts']} facts {s['by_confidence']}, "
          f"{s['verified']}/{s['facts']} verified, {s['unclaimed_figures']} figures unclaimed, "
          f"checks failed={s['checks_failed']} warn={s['checks_warn']}")
    print(f"  -> {md_path}\n  -> {facts_path}")
    snapshot = build_snapshot(transcript, result)
    snap_md, snap_json = write_snapshot(transcript.filename, snapshot, result, str(OUTPUT_FOLDER))
    ss = snapshot["stats"]
    print(f"  snapshot: {ss['signals']} management statements analysed, {ss['selected']} shown, "
          f"{ss['trajectory_rows']} reported-vs-guided rows, checks failed={ss['checks_failed']} warn={ss['checks_warn']}")
    print(f"  -> {snap_md}\n  -> {snap_json}")
    if audit:
        for u in result["unclaimed"]:
            print(f"  [unclaimed:{u['reason']}] {u['figure_text']!r}  in: {u['sentence'][:140]}")


def run(av_requests: list[tuple[str, str]], use_demo: bool, refresh: bool, audit: bool,
        fiscal_label: bool = False) -> int:
    transcripts = []

    for symbol, quarter in av_requests:
        try:
            cq = periods.from_fiscal(symbol, quarter) if fiscal_label else periods.resolve(symbol, quarter)
        except ValueError as exc:
            print(f"Bad quarter for {symbol}: {exc}", file=sys.stderr)
            return 1
        if not fiscal_label:
            print(f"{cq.symbol}: calendar {cq.calendar_label} -> {cq.caption()}; asking Alpha Vantage for {cq.fiscal_label}")
        try:
            item = fetch_from_alphavantage(cq.symbol, cq.fiscal_label, AV_CACHE_FOLDER, use_demo_key=use_demo,
                                           refresh=refresh)
        except AlphaVantageError as exc:
            print(f"Could not fetch {symbol} {quarter} (Alpha Vantage label {cq.fiscal_label}): {exc}", file=sys.stderr)
            return 1
        origin = "cache" if item["source_meta"]["from_cache"] else "Alpha Vantage"
        print(f"Loaded {cq.symbol} {cq.fiscal_label} from {origin}")
        transcripts.append(build_from_structured(item["turns"], item["filename"], {**item["source_meta"], **cq.as_meta()}))

    if not av_requests:
        if TRANSCRIPT_FOLDER.exists():
            for t in fetch_from_folder(str(TRANSCRIPT_FOLDER)):
                transcripts.append(parse_transcript(t["text"], t["filename"]))
        for path in sorted(AV_CACHE_FOLDER.glob("*.json")) if AV_CACHE_FOLDER.exists() else []:
            payload = json.loads(path.read_text(encoding="utf-8"))
            transcripts.append(build_from_structured(payload["transcript"], path.name,
                                                     {"quarter_label": payload.get("quarter")}))

    if not transcripts:
        print(f"Nothing to process. Add .txt files to {TRANSCRIPT_FOLDER} or use --av SYMBOL YYYYQn.")
        return 0

    for t in transcripts:
        _process(t, audit)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Earnings call fact extractor")
    ap.add_argument("--av", nargs=2, action="append", metavar=("SYMBOL", "YYYYQn"),
                    help="fetch a transcript from Alpha Vantage (repeatable); the quarter is a CALENDAR quarter")
    ap.add_argument("--fiscal-label", action="store_true",
                    help="treat the --av quarters as the company's own fiscal labels (what Alpha Vantage uses)")
    ap.add_argument("--demo", action="store_true", help="use the Alpha Vantage demo key (IBM 2024Q1 only)")
    ap.add_argument("--refresh", action="store_true", help="re-download even if cached")
    ap.add_argument("--audit", action="store_true", help="print figures that were not captured as facts")
    args = ap.parse_args()
    return run([tuple(x) for x in (args.av or [])], args.demo, args.refresh, args.audit, args.fiscal_label)


if __name__ == "__main__":
    sys.exit(main())

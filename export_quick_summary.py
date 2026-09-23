"""
export_quick_summary.py
Stage 1 of the "generalize the quick-summary template to any company" pipeline (see
docs/summary_template.md). Reads a gold-validated symbol-quarter (gold/<SYMBOL>_<QUARTER>.json
-- the hand-verified answer key, the same trust boundary this whole project gates a DB load on)
plus this project's own live data sources (sec_edgar.py, estimates.py, MarketDataLibrary's
insider-transactions fetch), and writes one clean JSON payload per symbol-quarter to
output/quick_summary/<SYMBOL>_<QUARTER>.json.

MarketDataLibrary's add_earnings_summaries_data.py (a separate repo) reads these JSON files
directly -- plain file reads, no cross-repo Python import for the DB-facing side, same reasoning
add_insider_transactions_data.py already documents for why it duplicates fetchers.py's
scrub_secrets instead of importing it: earnings_summarizer computes, MarketDataLibrary stores,
ModernPORTtheory exports for the static site. (This script DOES import MarketDataLibrary's
insider fetcher directly, by path -- it's the computation step, not the storage step, and
duplicating a whole AV request/parse implementation a second time isn't worth it just to avoid
one intentional cross-repo import at the one place that already needs both.)

Every number in the output is real: gold facts (verified, never re-derived), SEC EDGAR (free,
always attempted), Alpha Vantage estimates/insider activity (best-effort -- a fetch failure,
including a daily-quota 429, just means that section is left out of the payload for this
symbol, never fabricated or silently retried). Narrative sections (segment drivers, competitive
environment, product development, macro/regulatory) are deliberately NOT included yet -- see
docs/summary_template.md's Phase 2 (not built).

    py export_quick_summary.py AAPL_Q3_2026              one symbol-quarter (a gold/ filename stem)
    py export_quick_summary.py --all                     every gold-validated symbol-quarter
    py export_quick_summary.py AAPL_Q3_2026 --skip-av     SEC + gold facts only, no Alpha Vantage calls
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import estimates
import periods
import sec_edgar

ROOT = Path(__file__).resolve().parent
GOLD_DIR = ROOT / "gold"
OUT_DIR = ROOT / "output" / "quick_summary"


def _split_stem(stem: str) -> tuple[str, str]:
    """'AAPL_Q3_2026' -> ('AAPL', 'Q3_2026'); 'AMD_2026Q2' -> ('AMD', '2026Q2'). Both quarter
    tokens are exactly what periods.parse_quarter already accepts."""
    symbol, _, quarter_text = stem.partition("_")
    if not quarter_text:
        raise ValueError(f"Can't split symbol/quarter out of gold filename stem {stem!r}")
    return symbol, quarter_text


def _approx_period_end(symbol: str, quarter_text: str) -> date:
    """A starting guess only -- sec_edgar.py's own _refine_period_end() pins it to the real
    filed date from there, same as batch.py's approximation for live calls. The gold filename's
    quarter token is the COMPANY'S OWN fiscal label (confirmed against every fact id in
    output/<stem>_facts.json, e.g. "AAPL_2026_3-001" for gold/AAPL_Q3_2026.json) -- resolve via
    from_fiscal(), not resolve() (which would treat it as a calendar label and silently pick the
    wrong quarter for any company whose fiscal year doesn't end in December, e.g. AAPL)."""
    year, q = periods.parse_quarter(quarter_text)
    cq = periods.from_fiscal(symbol, f"{year}Q{q}")
    last_day = calendar.monthrange(cq.period_end_year, cq.period_end_month)[1]
    return date(cq.period_end_year, cq.period_end_month, last_day)


def _ledger(gold: dict) -> dict[tuple, dict]:
    """{(kind, metric, segment, stat): fact}. `stat` is part of the key on purpose -- gold files
    carry both a 'level' fact (the dollar figure) and a separate 'growth' fact (the yoy % as its
    own row) for the same (kind, metric, segment), e.g. iPhone revenue has both a level=$54.3B
    fact and a growth=22 fact; without `stat` in the key the second silently overwrites the
    first in this dict (a real bug caught by inspecting real output, not by reasoning about it)."""
    return {(f["kind"], f["metric"], f.get("segment") or "total", f.get("stat")): f for f in gold["expected"]}


def _fmt_change(change: dict | None) -> str | None:
    if not change or change.get("value") is None:
        return None
    val, unit, basis = change["value"], change.get("unit"), change.get("basis")
    sign = "+" if val >= 0 else ""
    if unit == "bps":
        return f"{sign}{val:.0f} bps {basis}"
    if unit == "pct":
        return f"{sign}{val:.0f}% {basis}"
    return f"{sign}{val} {unit} {basis}"


def _fmt_guidance_value(metric: str, guide: dict) -> str:
    v, v_high = guide["value"], guide.get("value_high")
    if metric == "gross_margin" or guide.get("stat") == "growth":
        return f"{v:.0f}-{v_high:.0f}%" if v_high is not None else f"{v:.0f}%"
    if metric == "eps":
        return f"${v:.2f}" + (f"-${v_high:.2f}" if v_high is not None else "")
    lo, hi = f"${v/1e9:.2f}B", (f"${v_high/1e9:.2f}B" if v_high is not None else None)
    return f"{lo}-{hi}" if hi else lo


_SCOREBOARD_METRICS = [
    ("revenue", "Revenue", lambda v: f"${v/1e9:.1f}B"),
    ("eps", "EPS", lambda v: f"${v:.2f}"),
    ("gross_margin", "Gross margin", lambda v: f"{v:.1f}%"),
    ("operating_cash_flow", "Operating cash flow", lambda v: f"${v/1e9:.1f}B"),
]
_SEGMENT_EXCLUDE = {"total", "products", "services"}   # aggregates, not individual segments
_LOWERCASE_WORDS = {"and", "of", "the", "for", "in"}


def _display_name(segment: str) -> str:
    """gold segment strings are raw lowercase, e.g. 'wearables, home, and accessories' --
    str.title() capitalizes every word including connectors ('And') and mishandles the comma.
    A small, generic (not AAPL-specific) fix: capitalize each word except common connectors,
    unless it's the first word."""
    words = segment.split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip(",")
        cased = core if (i > 0 and core.lower() in _LOWERCASE_WORDS) else core.capitalize()
        out.append(cased + ("," if w.endswith(",") else ""))
    return " ".join(out)


def build_scoreboard(ledger: dict) -> list[dict]:
    tiles = []
    for metric, label, fmt in _SCOREBOARD_METRICS:
        f = ledger.get(("reported", metric, "total", "level"))
        if f is None or f.get("value") is None:
            continue
        tile = {"label": label, "value": fmt(f["value"]), "delta": _fmt_change(f.get("change"))}

        est = ledger.get(f"_estimate_{metric}")
        if est is not None:
            s = estimates.surprise(f["value"], est)
            if s["pct"] is not None:
                tile["beat"] = f"{'Beat' if s['pct'] >= 0 else 'Missed'} consensus by {s['pct']:+.1f}%"
                tile["beat_good"] = s["pct"] >= 0

        # revenue guidance is filed as a growth-rate fact, not a level -- every other guided
        # metric here is a level (see _fmt_guidance_value)
        guide_stat = "growth" if metric == "revenue" else "level"
        guide = ledger.get(("guidance", metric, "total", guide_stat))
        next_est = ledger.get(f"_estimate_next_{metric}")
        if guide is not None and guide.get("value") is not None:
            tile["guidance"] = f"Next qtr guide: {_fmt_guidance_value(metric, guide)}"
        elif next_est is not None:
            tile["guidance"] = f"Street next qtr: {fmt(next_est)} (no company guide)"
        tiles.append(tile)
    return tiles


def build_segments(ledger: dict) -> list[dict]:
    rows = []
    for key, f in ledger.items():
        if not (isinstance(key, tuple) and len(key) == 4):
            continue                                       # skip the "_estimate_*" scalar keys
        kind, metric, segment, stat = key
        if kind != "reported" or metric != "revenue" or stat != "level" or segment in _SEGMENT_EXCLUDE:
            continue
        if f.get("value") is None:
            continue
        change = f.get("change") or {}
        rows.append({"name": segment.title(), "revenue": f["value"], "yoy_pct": change.get("value")})
    rows.sort(key=lambda r: -r["revenue"])
    return rows


def build_capital_allocation(facts: dict, ledger: dict, period_end: date) -> dict:
    uses = {}
    for metric, label in (("dividends", "Dividends paid"), ("share_repurchases", "Share repurchases")):
        f = ledger.get(("reported", metric, "total", "level"))
        if f and f.get("value") is not None:
            uses[label] = f["value"]
    for metric, label in (("capex", "CapEx"), ("debt_repaid", "Debt repaid")):
        v = sec_edgar.find_quarterly_value(facts, metric, period_end)
        if v is not None:
            uses[label] = v["value"]
    return uses


_MATURITY_LABELS = [("debt_maturity_1y", "Next 12mo"), ("debt_maturity_2y", "Year 2"),
                    ("debt_maturity_3y", "Year 3"), ("debt_maturity_4y", "Year 4"),
                    ("debt_maturity_5y", "Year 5"), ("debt_maturity_after_5y", "After 5yr")]


def build_balance_sheet(facts: dict, ledger: dict) -> dict:
    out = {}
    cash = ledger.get(("reported", "cash_and_securities", "total", "level"))
    debt = ledger.get(("reported", "total_debt", "total", "level"))
    if cash and cash.get("value") is not None:
        out["cash"] = cash["value"]
    if debt and debt.get("value") is not None:
        out["debt"] = debt["value"]
    if "cash" in out and "debt" in out:
        out["net_cash"] = out["cash"] - out["debt"]

    ladder, as_of = {}, None
    for metric, label in _MATURITY_LABELS:
        spec = sec_edgar.metric_map().get(metric)
        if not spec:
            continue
        entries = sec_edgar._entries_for(facts, spec["concepts"], spec["unit"])
        latest = sorted(entries, key=lambda e: e.get("end", ""), reverse=True)[:1]
        if latest:
            ladder[label] = latest[0]["val"]
            as_of = as_of or latest[0].get("end")
    if ladder:
        out["debt_maturity"] = ladder
        out["debt_maturity_as_of"] = as_of
    return out


def build_insider_activity(symbol: str, quarter_start: date, quarter_end: date,
                           api_key: str) -> dict | None:
    sys.path.insert(0, str(ROOT.parent / "MarketDataLibrary"))
    import add_insider_transactions_data as insider   # separate repo -- see module docstring

    raw = insider.fetch_insider_transactions(symbol, api_key)
    rows = []
    for r in raw:
        parsed = insider._parse_row(symbol, r)
        if parsed is None or not parsed["transaction_date"]:
            continue
        parsed["d"] = parsed["transaction_date"]
        rows.append(parsed)

    sales = [r for r in rows if quarter_start <= r["d"] <= quarter_end
            and r["security_type"] == "Common Stock" and r["acquisition_or_disposal"] == "D"
            and r["share_price"] not in (None, 0.0)]
    if not sales:
        return None
    by_exec: dict[str, dict] = {}
    for r in sales:
        e = by_exec.setdefault(r["executive"], {"title": r["executive_title"], "shares": 0.0, "value": 0.0})
        e["shares"] += r["shares"]
        e["value"] += r["shares"] * r["share_price"]
    return {
        "window_start": quarter_start.isoformat(), "window_end": quarter_end.isoformat(),
        "total_value": sum(v["value"] for v in by_exec.values()),
        "total_shares": sum(v["shares"] for v in by_exec.values()),
        "by_executive": sorted(({"name": n, **v} for n, v in by_exec.items()), key=lambda x: -x["value"]),
    }


def export_one(stem: str, skip_av: bool = False) -> Path:
    gold = json.loads((GOLD_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    symbol, quarter_text = _split_stem(stem)
    ledger = _ledger(gold)
    period_end = _approx_period_end(symbol, quarter_text)
    # ~91 days before the (fiscal) period end, not derived from the raw calendar-quarter number
    # -- that inverted the range for any company whose fiscal quarter doesn't align to the
    # calendar (e.g. AAPL fiscal Q3 ends in June, not September), silently zeroing every match
    quarter_start = period_end - timedelta(days=91)

    if not skip_av:
        api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
        if api_key:
            try:
                est_rows = estimates.fetch_estimates(symbol)
                this_q = estimates.find_estimate(est_rows, period_end, tolerance_days=100)
                if this_q:
                    if this_q.get("revenue") is not None:
                        ledger["_estimate_revenue"] = this_q["revenue"]
                    if this_q.get("eps") is not None:
                        ledger["_estimate_eps"] = this_q["eps"]
            except Exception as exc:
                print(f"  {stem}: estimates fetch failed ({type(exc).__name__}: {exc})", file=sys.stderr)

    payload = {
        "symbol": symbol,
        "quarter": quarter_text,
        "period_end_guess": period_end.isoformat(),
        "scoreboard": build_scoreboard(ledger),
        "segments": build_segments(ledger),
    }

    try:
        facts = sec_edgar.fetch_company_facts(symbol)
        payload["capital_allocation"] = build_capital_allocation(facts, ledger, period_end)
        payload["balance_sheet"] = build_balance_sheet(facts, ledger)
    except sec_edgar.SECEdgarError as exc:
        print(f"  {stem}: SEC EDGAR fetch failed ({exc})", file=sys.stderr)
        payload["capital_allocation"] = {}
        payload["balance_sheet"] = {}

    if not skip_av and os.environ.get("ALPHAVANTAGE_API_KEY"):
        try:
            insider_payload = build_insider_activity(symbol, quarter_start, period_end,
                                                      os.environ["ALPHAVANTAGE_API_KEY"])
            if insider_payload:
                payload["insider_activity"] = insider_payload
        except Exception as exc:
            print(f"  {stem}: insider-activity fetch failed ({type(exc).__name__}: {exc})", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{stem}.json"
    out_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stem", nargs="?", help="a gold/ filename stem, e.g. AAPL_Q3_2026")
    ap.add_argument("--all", action="store_true", help="every gold-validated symbol-quarter")
    ap.add_argument("--skip-av", action="store_true", help="SEC + gold facts only, no Alpha Vantage calls")
    args = ap.parse_args()

    if args.all:
        stems = sorted(p.stem for p in GOLD_DIR.glob("*.json") if p.stem != "PENDING")
    elif args.stem:
        stems = [args.stem]
    else:
        ap.error("pass a stem or --all")
        return 1

    ok = 0
    for stem in stems:
        try:
            out = export_one(stem, skip_av=args.skip_av)
            print(f"{stem}: wrote {out}")
            ok += 1
        except Exception as exc:
            print(f"{stem}: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
    print(f"\n{ok}/{len(stems)} symbol-quarters exported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

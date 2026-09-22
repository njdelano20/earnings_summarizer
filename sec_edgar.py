"""
sec_edgar.py
Independent ground truth for the headline financial figures the extractor pulls from a call: SEC EDGAR's XBRL
"company facts" API returns every number a company has ever formally filed, machine-tagged, including standalone
QUARTERLY figures (data/raw/alphavantage's calls have no equivalent in the MarketDataLibrary: its financials/segments
tables are FY and TTM only -- confirmed 2026-09-22, no quarterly granularity at all -- so this fills a real gap,
not a duplicate of what the library already has).

Deliberately limited to metrics with one clean, near-universal GAAP tag (see sec/metrics.json): revenue, net
income, operating income, gross profit, EPS. No total debt, no free cash flow, no vocabulary-pack metrics (AFFO,
NII, combined ratio, ...) -- none of those have a single tag every filer uses, so a mapping table would just
introduce new mismatches. No segment-level data either: that needs XBRL dimensional parsing (a materially bigger,
separate task) or the library's own segments table, which has known data-quality issues -- left for later, not
guessed at now.

No API key, no daily quota (unlike Alpha Vantage / Gemini) -- SEC EDGAR is free and public. It DOES require every
request to carry a descriptive User-Agent identifying the requester (SEC's own fair-access policy:
https://www.sec.gov/os/webmaster-faq#developers) and asks for no more than ~10 requests/second. Set
SEC_EDGAR_CONTACT to "Your Name your@email.com" -- never hardcoded; read from the environment only, the same
discipline as ALPHAVANTAGE_API_KEY / GEMINI_API_KEY, since this is sent to a third party on every request.

Also uses SEC's submissions API (a company's full filing history -- form, filing date, and the actual period the
filing COVERS) to sharpen the quarter match: the caller (batch.py) only knows a call's quarter as year+month
(periods.py tracks no exact day) and approximates the period-end as that month's last calendar day, which can be a
few days off a real 52/53-week fiscal calendar. `_refine_period_end()` looks up the nearest real 10-Q/10-K
`reportDate` to that guess and, when one is close enough to trust, matches against it exactly instead of the fuzzy
approximation -- never required (falls back to the original guess with the wider tolerance if submissions has
nothing close, or the lookup fails for any reason).

    py sec_edgar.py AMD                     fetch/cache AMD's XBRL company facts, print what's available
    py sec_edgar.py AMD --check HESM_2026Q2  won't do anything for AMD/HESM mismatch -- see batch.py, which wires
                                             the real per-call check in automatically

CIK codes come from sec/ciks.json (refresh_ciks.py, from the MarketDataLibrary's profile.cik_code -- 99.7% of
common stocks already have one). Raw responses cached in data/raw/sec/, tracked in git on purpose (same convention
as data/raw/alphavantage/): the companyfacts endpoint returns a company's ENTIRE filing history in one call, so one
cache file per symbol serves every quarter's check going forward, not just today's.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CIKS_FILE = ROOT / "sec" / "ciks.json"
METRICS_FILE = ROOT / "sec" / "metrics.json"
CACHE_DIR = ROOT / "data" / "raw" / "sec"
EDGAR_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DEFAULT_CONTACT = "earnings_summarizer (contact not set: set SEC_EDGAR_CONTACT to 'Name email@example.com')"
# how far the caller's crude year+month guess may be from a real filed reportDate before that filing is not
# trusted as "the same quarter" -- wide enough to survive fiscal-calendar drift, tight enough to never cross into
# an adjacent quarter (quarters are ~91 days apart, so half that is a safe ceiling).
_REFINE_SEARCH_DAYS = 45

# A quarterly (not YTD, not annual) duration is close to 91 days; SEC filings vary a little either side.
_QUARTER_DAYS = (75, 100)
_DEFAULT_TOLERANCE_DAYS = 3
# How far apart two independently reported figures for the same period can be before it's a real mismatch, not
# rounding noise (the call often states a rounded figure, e.g. "$11.5 billion" vs XBRL's exact 11,536,000,000).
_RELATIVE_TOLERANCE = 0.02
# XBRL's us-gaap tags are always STRICTLY GAAP; a company's call very often leads with non-GAAP figures without
# saying so on every line (AMD literally opens by saying "we will refer primarily to non-GAAP financial measures").
# Revenue rarely differs GAAP vs non-GAAP, so it is always compared directly. Anything else is only compared as a
# real pass/warn when the extracted fact is confirmed GAAP (f["accounting"] == "gaap"); otherwise a mismatch is
# real information (the two numbers genuinely differ) but not a claim that either one is wrong.
_ALWAYS_COMPARABLE = {"revenue"}

_cache: dict[str, dict] = {}


class SECEdgarError(RuntimeError):
    """EDGAR answered, but with an error, or not in the shape expected."""


def _load_json(path: Path, default):
    key = str(path)
    if key not in _cache:
        try:
            _cache[key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        except (OSError, ValueError):
            _cache[key] = default
    return _cache[key]


def cik_for(symbol: str | None) -> str | None:
    if not symbol:
        return None
    return _load_json(CIKS_FILE, {}).get(symbol.strip().upper())


def metric_map() -> dict[str, dict]:
    return _load_json(METRICS_FILE, {}).get("metrics", {})


def _contact(contact: str | None = None) -> str:
    import os
    return contact or os.environ.get("SEC_EDGAR_CONTACT") or DEFAULT_CONTACT


def fetch_company_facts(symbol: str, cache_dir: Path = CACHE_DIR, contact: str | None = None,
                        refresh: bool = False, max_age_days: int = 3) -> dict:
    """Every XBRL fact SEC has for this symbol's CIK, ever filed. Cached (one file per symbol); a cache older than
    `max_age_days` is treated as stale (a new filing may have appeared) unless `refresh` forces it either way."""
    cik = cik_for(symbol)
    if not cik:
        raise SECEdgarError(f"No CIK on file for {symbol!r} (run py refresh_ciks.py, or the symbol is not a common "
                            f"stock in the MarketDataLibrary).")
    cache_path = Path(cache_dir) / f"{symbol.strip().upper()}.json"
    if cache_path.exists() and not refresh:
        age = date.today() - date.fromtimestamp(cache_path.stat().st_mtime)
        if age <= timedelta(days=max_age_days):
            return json.loads(cache_path.read_text(encoding="utf-8"))
    payload = _request(EDGAR_URL.format(cik=cik), _contact(contact))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def fetch_submissions(symbol: str, cache_dir: Path = CACHE_DIR, contact: str | None = None,
                      refresh: bool = False, max_age_days: int = 3) -> dict:
    """This symbol's full SEC filing history (form, filing date, and the period each filing COVERS). Cached
    separately from company facts, same freshness rule."""
    cik = cik_for(symbol)
    if not cik:
        raise SECEdgarError(f"No CIK on file for {symbol!r} (run py refresh_ciks.py, or the symbol is not a common "
                            f"stock in the MarketDataLibrary).")
    cache_path = Path(cache_dir) / "submissions" / f"{symbol.strip().upper()}.json"
    if cache_path.exists() and not refresh:
        age = date.today() - date.fromtimestamp(cache_path.stat().st_mtime)
        if age <= timedelta(days=max_age_days):
            return json.loads(cache_path.read_text(encoding="utf-8"))
    payload = _request(EDGAR_SUBMISSIONS_URL.format(cik=cik), _contact(contact))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def _request(url: str, contact: str) -> dict:
    import requests

    resp = requests.get(url, headers={"User-Agent": contact}, timeout=30)
    if resp.status_code == 404:
        raise SECEdgarError(f"SEC EDGAR returned 404 for {url} (not all filers have this).")
    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise SECEdgarError(f"SEC EDGAR request failed ({type(exc).__name__}): {exc}") from None
    try:
        return resp.json()
    except ValueError as exc:
        raise SECEdgarError(f"Non-JSON response from SEC EDGAR: {resp.text[:200]!r}") from exc


def _refine_period_end(symbol: str, approx_period_end: date, cache_dir: Path = CACHE_DIR,
                       contact: str | None = None) -> tuple[date, bool]:
    """(period_end, exact). Looks up the nearest real 10-Q/10-K reportDate to the caller's guess; exact=True only
    when one was found within _REFINE_SEARCH_DAYS, meaning the caller can match against it tightly instead of the
    fuzzy month-end approximation. Never raises -- any lookup failure just means exact=False."""
    try:
        subs = fetch_submissions(symbol, cache_dir=cache_dir, contact=contact)
    except SECEdgarError:
        return approx_period_end, False
    recent = subs.get("filings", {}).get("recent", {})
    forms, report_dates = recent.get("form", []), recent.get("reportDate", [])
    best, best_gap = None, None
    for form, rd in zip(forms, report_dates):
        if form not in ("10-Q", "10-K") or not rd:
            continue
        try:
            d = date.fromisoformat(rd)
        except ValueError:
            continue
        gap = abs((d - approx_period_end).days)
        if gap <= _REFINE_SEARCH_DAYS and (best_gap is None or gap < best_gap):
            best, best_gap = d, gap
    return (best, True) if best is not None else (approx_period_end, False)


# --------------------------------------------------------------------------- #
# finding the value for one metric and one quarter
# --------------------------------------------------------------------------- #

def _entries_for(facts: dict, concepts: list[str], unit: str) -> list[dict]:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    xbrl_unit = "USD/shares" if unit == "USD_per_share" else unit
    for name in concepts:
        node = us_gaap.get(name)
        if node and xbrl_unit in node.get("units", {}):
            return node["units"][xbrl_unit]
    return []


def find_quarterly_value(facts: dict, metric: str, period_end: date, tolerance_days: int = _DEFAULT_TOLERANCE_DAYS
                         ) -> dict | None:
    """The single-quarter (not YTD, not annual) value for `metric` whose period ends within `tolerance_days` of
    `period_end`. None if nothing matches (the filing may not exist yet, or use a tag this mapping doesn't know)."""
    spec = metric_map().get(metric)
    if not spec:
        return None
    entries = _entries_for(facts, spec["concepts"], spec["unit"])
    candidates = []
    for e in entries:
        try:
            end = date.fromisoformat(e["end"])
        except (KeyError, ValueError):
            continue
        if abs((end - period_end).days) > tolerance_days:
            continue
        if spec["kind"] == "duration":
            try:
                start = date.fromisoformat(e["start"])
            except (KeyError, ValueError):
                continue
            days = (end - start).days
            if not (_QUARTER_DAYS[0] <= days <= _QUARTER_DAYS[1]):
                continue                                  # a YTD or annual duration, not a standalone quarter
        candidates.append(e)
    if not candidates:
        return _derive_quarter_from_ytd(facts, metric, period_end, tolerance_days)
    # prefer the filing closest to the exact date, then an un-amended 10-Q/10-K over a 10-Q/A
    candidates.sort(key=lambda e: (abs((date.fromisoformat(e["end"]) - period_end).days), e.get("form", "").endswith("/A")))
    best = candidates[0]
    return {"value": best["val"], "unit": spec["unit"], "end": best["end"], "start": best.get("start"),
           "form": best.get("form"), "fy": best.get("fy"), "fp": best.get("fp"), "filed": best.get("filed"),
           "accn": best.get("accn"), "derived_from_ytd": False}


def _derive_quarter_from_ytd(facts: dict, metric: str, period_end: date,
                             tolerance_days: int = _DEFAULT_TOLERANCE_DAYS) -> dict | None:
    """Cash-flow-statement concepts (capex, debt issued/repaid, acquisitions, ...) are typically tagged
    CUMULATIVE-YTD ONLY in a 10-Q -- SEC's own interim-reporting convention (ASC 270), unlike income-statement
    concepts, which usually also get a standalone-quarter tag. `find_quarterly_value` calls this only after its
    own direct standalone-quarter search comes up empty. Derives the quarter as YTD(this period) minus
    YTD(the immediately prior quarter, same fiscal-year start) -- both real filed numbers, nothing invented.
    None if no matching prior-quarter YTD entry exists (e.g. Q1, where YTD already equals the quarter and would
    have been caught by the direct search already, or the prior filing simply isn't in this cache)."""
    spec = metric_map().get(metric)
    if not spec or spec["kind"] != "duration":
        return None
    entries = _entries_for(facts, spec["concepts"], spec["unit"])
    this_ytd = None
    for e in entries:
        try:
            end = date.fromisoformat(e["end"])
            start = date.fromisoformat(e["start"])
        except (KeyError, ValueError):
            continue
        if abs((end - period_end).days) > tolerance_days:
            continue
        days = (end - start).days
        if days <= _QUARTER_DAYS[1]:
            continue                                  # a standalone quarter -- the direct search already covers this
        # prefer the shortest qualifying cumulative span, so an annual 10-K entry never wins over a 10-Q's YTD
        if this_ytd is None or days < (date.fromisoformat(this_ytd["end"]) - date.fromisoformat(this_ytd["start"])).days:
            this_ytd = e
    if this_ytd is None:
        return None
    this_start = date.fromisoformat(this_ytd["start"])
    this_end = date.fromisoformat(this_ytd["end"])
    this_days = (this_end - this_start).days
    target_prior_days = this_days - 91                # one quarter (~91 days) shorter, same start
    best_prior, best_gap = None, None
    for e in entries:
        try:
            end = date.fromisoformat(e["end"])
            start = date.fromisoformat(e["start"])
        except (KeyError, ValueError):
            continue
        if start != this_start or end >= this_end:
            continue
        gap = abs((end - start).days - target_prior_days)
        if gap <= 20 and (best_gap is None or gap < best_gap):
            best_prior, best_gap = e, gap
    if best_prior is None:
        return None
    return {"value": this_ytd["val"] - best_prior["val"], "unit": spec["unit"], "end": this_ytd["end"],
           "start": best_prior["end"], "form": this_ytd.get("form"), "fy": this_ytd.get("fy"),
           "fp": this_ytd.get("fp"), "filed": this_ytd.get("filed"), "accn": this_ytd.get("accn"),
           "derived_from_ytd": True}


# --------------------------------------------------------------------------- #
# the cross-check
# --------------------------------------------------------------------------- #

def check_against_sec(facts_result: dict, symbol: str, period_end: date, cache_dir: Path = CACHE_DIR,
                      contact: str | None = None) -> list[dict]:
    """One check entry per headline metric this call reported at the company-total level: 'pass' (within tolerance
    of the SEC-filed figure), 'warn' (a real mismatch), or 'no_data' (no CIK, no filing yet, or no matching tag --
    never treated as a failure, since a 10-Q is often filed days to weeks after the call)."""
    checks = []
    try:
        edgar_facts = fetch_company_facts(symbol, cache_dir=cache_dir, contact=contact)
    except SECEdgarError as exc:
        return [{"name": "sec_xbrl", "status": "no_data", "detail": str(exc)}]

    exact_period_end, is_exact = _refine_period_end(symbol, period_end, cache_dir=cache_dir, contact=contact)
    tolerance_days = 1 if is_exact else _DEFAULT_TOLERANCE_DAYS

    ledger = {f["metric"]: f for f in facts_result["facts"]
             if f["kind"] == "reported" and f["stat"] == "level" and (f["segment"] or "total") == "total"}
    for metric in sorted(metric_map()):
        f = ledger.get(metric)
        if f is None or f["value"] is None:
            continue                                       # nothing extracted for this metric: nothing to check
        sec_fact = find_quarterly_value(edgar_facts, metric, exact_period_end, tolerance_days=tolerance_days)
        if sec_fact is None:
            checks.append({"name": f"sec_xbrl[{metric}]", "status": "no_data",
                          "detail": f"no matching SEC quarterly figure for {metric} near "
                                    f"{exact_period_end.isoformat()} (filing may not be out yet)"})
            continue
        extracted, sec_value = f["value"], sec_fact["value"]
        rel_diff = abs(extracted - sec_value) / max(1.0, abs(sec_value))
        gaap_comparable = metric in _ALWAYS_COMPARABLE or f.get("accounting") == "gaap"
        if rel_diff <= _RELATIVE_TOLERANCE:
            status, note = "pass", ""
        elif gaap_comparable:
            status, note = "warn", ""
        else:
            # a real numeric difference, but the extracted figure isn't confirmed GAAP, so it may just be the
            # company's own non-GAAP figure versus SEC's always-GAAP tag -- genuinely ambiguous, not a red flag
            status = "unlabeled_accounting_basis"
            note = " (extracted fact has no confirmed GAAP/non-GAAP label -- this may be an expected non-GAAP " \
                  "figure compared against SEC's GAAP-only tag, not necessarily an extraction error)"
        checks.append({"name": f"sec_xbrl[{metric}]", "status": status,
                      "detail": f"extracted {extracted:,.0f} vs SEC {sec_value:,.0f} ({sec_fact['form']}, "
                                f"period ended {sec_fact['end']}): {rel_diff:.1%} apart{note}",
                      "extracted": extracted, "sec_value": sec_value, "sec_form": sec_fact["form"],
                      "sec_period_end": sec_fact["end"], "fact_id": f["id"]})
    return checks


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    cik = cik_for(args.symbol)
    if not cik:
        print(f"No CIK on file for {args.symbol!r}. Run: py refresh_ciks.py", file=sys.stderr)
        return 1
    try:
        facts = fetch_company_facts(args.symbol, refresh=args.refresh)
    except SECEdgarError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{args.symbol} (CIK {cik}): {facts.get('entityName')}")
    for metric, spec in sorted(metric_map().items()):
        entries = _entries_for(facts, spec["concepts"], spec["unit"])
        recent = sorted((e for e in entries if e.get("form") in ("10-Q", "10-K")), key=lambda e: e["end"], reverse=True)[:4]
        print(f"  {metric}: {len(entries)} total entries; most recent quarterly/annual filings:")
        for e in recent:
            print(f"    {e.get('start', '?')} to {e['end']}: {e['val']:,} ({e.get('form')}, filed {e.get('filed')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

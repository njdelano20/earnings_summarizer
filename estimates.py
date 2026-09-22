"""
estimates.py
Analyst consensus estimates (EPS + revenue, quarterly and annual) from Alpha Vantage's
EARNINGS_ESTIMATES function -- real "what was expected" data, filling a gap this project's
docs previously just noted as absent ("no consensus data in this pipeline"). Used for two
things:
  1. Headline-scoreboard beat/miss for the quarter just reported (actual vs. what the Street
     already expected going into the print).
  2. Checking whether NEW forward guidance is raised/lowered/in-line -- against what analysts
     already expected for that future period, not against the quarter that just happened.
     Comparing new guidance to the OLD quarter's actual only shows deceleration/acceleration,
     not whether the company is guiding above or below where the Street already was.

Deliberately limited to EPS and revenue -- AV's EARNINGS_ESTIMATES has no gross-margin or
operating-cash-flow estimate field at all (confirmed against the real response shape), so
those stay marked "no consensus available" rather than derived or guessed from EPS/revenue.

Same key/quota discipline as fetchers.py: ALPHAVANTAGE_API_KEY only, shared ~25 req/day free
tier, raw responses cached so a repeat run costs no quota.

    py estimates.py AAPL              fetch/cache, print the estimate rows nearest each quarter
    py estimates.py AAPL --refresh    force a fresh fetch
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from fetchers import ALPHAVANTAGE_URL, AlphaVantageError, scrub_secrets

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "raw" / "alphavantage" / "estimates"
_DEFAULT_TOLERANCE_DAYS = 5


def fetch_estimates(symbol: str, api_key: str | None = None, cache_dir: Path = CACHE_DIR,
                    refresh: bool = False, max_age_days: int = 3) -> list[dict]:
    """Every EPS/revenue estimate row AV has for this symbol (quarterly + annual, current +
    historical). Cached like sec_edgar.py's company facts -- one file covers every quarter's
    lookup going forward."""
    symbol = symbol.strip().upper()
    cache_path = Path(cache_dir) / f"{symbol}.json"
    if cache_path.exists() and not refresh:
        age = date.today() - date.fromtimestamp(cache_path.stat().st_mtime)
        if age <= timedelta(days=max_age_days):
            return json.loads(cache_path.read_text(encoding="utf-8"))["estimates"]

    key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        raise AlphaVantageError("No API key. Set the ALPHAVANTAGE_API_KEY environment variable.")
    payload = _request(symbol, key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload["estimates"]


def _request(symbol: str, api_key: str) -> dict:
    import requests

    def scrub(text: str) -> str:
        return scrub_secrets(text, api_key)

    try:
        resp = requests.get(ALPHAVANTAGE_URL,
                            params={"function": "EARNINGS_ESTIMATES", "symbol": symbol, "apikey": api_key},
                            timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        raise AlphaVantageError(f"Alpha Vantage request failed ({type(exc).__name__}): "
                                f"{scrub(str(exc))}") from None
    try:
        data = resp.json()
    except ValueError:
        raise AlphaVantageError(f"Non-JSON response: {scrub(resp.text[:200])!r}") from None
    for msg_key in ("Information", "Note", "Error Message"):
        if msg_key in data:
            raise AlphaVantageError(scrub(f"Alpha Vantage: {data[msg_key]}"))
    if "estimates" not in data:
        raise AlphaVantageError(f"No 'estimates' field in response for {symbol}.")
    return data


def find_estimate(estimates: list[dict], period_end: date, horizon: str = "fiscal quarter",
                  tolerance_days: int = _DEFAULT_TOLERANCE_DAYS) -> dict | None:
    """The estimate row whose `date` is within `tolerance_days` of `period_end`, for the given
    horizon ("fiscal quarter" or "fiscal year"). None if nothing matches closely enough --
    never guessed from a neighboring period."""
    best, best_gap = None, None
    for e in estimates:
        if e.get("horizon") != horizon:
            continue
        try:
            d = date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        gap = abs((d - period_end).days)
        if gap <= tolerance_days and (best_gap is None or gap < best_gap):
            best, best_gap = e, gap
    if best is None:
        return None
    return {
        "period_end": best["date"],
        "eps": float(best["eps_estimate_average"]) if best.get("eps_estimate_average") else None,
        "revenue": float(best["revenue_estimate_average"]) if best.get("revenue_estimate_average") else None,
        "analyst_count": int(float(best["eps_estimate_analyst_count"])) if best.get("eps_estimate_analyst_count") else None,
    }


def surprise(actual: float, estimate: float) -> dict:
    """{"abs": actual-estimate, "pct": relative %} -- never divides by zero silently."""
    pct = (actual - estimate) / abs(estimate) * 100 if estimate else None
    return {"abs": actual - estimate, "pct": pct}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    try:
        rows = fetch_estimates(args.symbol, refresh=args.refresh)
    except AlphaVantageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    quarterly = [r for r in rows if r.get("horizon") == "fiscal quarter"]
    quarterly.sort(key=lambda r: r.get("date", ""), reverse=True)
    print(f"{args.symbol}: {len(quarterly)} quarterly estimate rows, most recent first:")
    for r in quarterly[:6]:
        print(f"  {r['date']}: EPS est {r.get('eps_estimate_average')}, "
              f"revenue est {r.get('revenue_estimate_average')}, "
              f"{r.get('eps_estimate_analyst_count')} analysts")
    return 0


if __name__ == "__main__":
    sys.exit(main())

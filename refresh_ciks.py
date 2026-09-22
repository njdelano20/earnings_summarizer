"""
refresh_ciks.py
Regenerates sec/ciks.json: every symbol's SEC CIK (Central Index Key), from the MarketDataLibrary's own
profile.cik_code (already populated for 5,356 of 5,370 common stocks via the existing stockanalysis.com scraping
work -- verified 2026-09-22, no backfill needed). sec_edgar.py reads this snapshot rather than holding a live
database connection at fetch/check time, same reasoning as vocab.py's symbol_industry.json.

    py refresh_ciks.py

The output is tracked in git on purpose (regenerable, but needed at runtime) -- same convention as
vocabulary/symbol_industry.json and data/raw/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import periods

OUT = Path(__file__).resolve().parent / "sec" / "ciks.json"


def main() -> int:
    try:
        import duckdb
    except ImportError:
        print("duckdb is not installed; run `py -m pip install duckdb`.", file=sys.stderr)
        return 1

    try:
        con = duckdb.connect(str(periods.LIBRARY_FILE), read_only=True)
    except Exception as exc:
        print(f"Could not open the MarketDataLibrary ({periods.LIBRARY_FILE}): {exc}", file=sys.stderr)
        print("(It may be locked by another process using it. Try again once it is free.)", file=sys.stderr)
        return 1

    try:
        rows = con.sql("""
            SELECT p.symbol, p.cik_code
            FROM profile p
            JOIN securities s ON s.symbol = p.symbol
            WHERE s.security_type = 'Common Stock' AND p.cik_code IS NOT NULL AND p.cik_code != ''
        """).fetchall()
    finally:
        con.close()

    # SEC's API wants the CIK zero-padded to 10 digits; the library may or may not already pad it.
    out = {sym: str(cik).strip().zfill(10) for sym, cik in rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} symbols -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

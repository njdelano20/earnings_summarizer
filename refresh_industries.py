"""
refresh_industries.py
Regenerates vocabulary/symbol_industry.json: every symbol's sector/industry (Yahoo Finance's own classification,
as already stored in the MarketDataLibrary) so vocab.py can resolve a ticker's vocabulary packs without a live
database connection at extraction time.

    py refresh_industries.py

The output is tracked in git on purpose (same reasoning as data/raw/ and gold/): it is regenerable, but the
extractor needs it at runtime and a live DuckDB connection is not always available (the file may be locked by
another process using the library). Re-run this after the library's sector/industry data changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import periods

OUT = Path(__file__).resolve().parent / "vocabulary" / "symbol_industry.json"


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
            SELECT symbol, sector, industry
            FROM securities
            WHERE security_type = 'Common Stock' AND (sector IS NOT NULL OR industry IS NOT NULL)
        """).fetchall()
    finally:
        con.close()

    out = {sym: {"sector": sector, "industry": industry} for sym, sector, industry in rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} symbols -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

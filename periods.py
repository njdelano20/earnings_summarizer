"""
periods.py
Calendar quarters in, company quarters out.

Every quarter label the program is given ("2026q2", "2026Q2", "Q2 2026") is a CALENDAR quarter on one assumed
Jan-Dec calendar (Q1 = Jan-Mar ... Q4 = Oct-Dec), the same for every company, so a run can be mass-produced across
symbols and joined on one time axis. This module turns it into the company's own fiscal quarter, whatever month its
year ends in:

    2026q2 for Copart (fiscal year ends July 31)  ->  fiscal 2026 Q4, the May-Jul 2026 quarter (reported mid-Sep)
    2026q2 for AMD (calendar year)                ->  Q2 2026, the Apr-Jun quarter (reported early Aug)

Rule: "2026q2" is every company's quarter that MOSTLY happened in April-June, i.e. whose MIDDLE month falls in it.
Results are always reported after the period closes ("Q2 results are recorded in Q3"), so the label follows the
months the results cover, not the date of the call, and each label collects one earnings season. A fiscal year-end
month m puts its quarter-ends in months m, m+3, m+6 and m+9, so the middle months are congruent mod 3 as well and
exactly one company quarter has its middle month in each calendar quarter, whatever m is: the mapping is
one-to-one with no special cases. (Where a quarter's months straddle two calendar quarters, the two-thirds
majority decides: Nov-Jan is Q4, Feb-Apr is Q1, May-Jul is Q2, Aug-Oct is Q3.)
Some labels become available before their season starts (Nike's Jun-Aug quarter is q3 but is reported in late
September): an automatic update should read "no transcript yet" as "try again later".

Two facts per company decide the answer, kept in fiscal_calendars.json:
  * fye_month  month the fiscal year ends in (nearest month-end for 52/53-week years: Apple's last Saturday of
               September is 9; a year ending "Jan 3" is really 12).
  * naming     "end"   the fiscal year is named for the calendar year it ends in (Apple, Copart, Walmart)
               "start" ...for the year it starts in (Target, Home Depot, Lowe's: fiscal 2025 ends Jan 2026)
A symbol with no entry falls back to the MarketDataLibrary (financials.period_ending), then to "calendar year".

Alpha Vantage's `quarter` parameter takes the company's own FISCAL label (verified on Copart, 2026-09-21), so
resolve() gives the label to send there; from_fiscal() goes the other way for files that already carry one.

    py periods.py CPRT 2026q2       what that calendar quarter is for Copart, and the label Alpha Vantage wants
    py periods.py CPRT              the last eight calendar quarters for Copart
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CALENDARS_FILE = ROOT / "fiscal_calendars.json"
LIBRARY_FILE = Path(os.environ.get("MARKET_DATA_LIBRARY")
                    or ROOT.parent / "MarketDataLibrary" / "MarketDataLibrary.duckdb")

_MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ASSUMED = "assumed calendar year (no fiscal calendar on file)"


@dataclass(frozen=True)
class FiscalCalendar:
    fye_month: int                   # 1-12
    naming: str = "end"              # "end" | "start"
    source: str = ASSUMED

    @property
    def assumed(self) -> bool:
        return self.source == ASSUMED


@dataclass(frozen=True)
class CompanyQuarter:
    symbol: str
    calendar_year: int
    calendar_quarter: int
    fiscal_year: int
    fiscal_quarter: int
    period_end_year: int
    period_end_month: int
    fye_month: int
    naming: str
    source: str

    @property
    def calendar_label(self) -> str:
        return f"{self.calendar_year}Q{self.calendar_quarter}"

    @property
    def fiscal_label(self) -> str:
        """The label Alpha Vantage takes (YYYYQn in the company's own fiscal numbering)."""
        return f"{self.fiscal_year}Q{self.fiscal_quarter}"

    @property
    def period_end_label(self) -> str:
        return f"{_MONTH[self.period_end_month - 1]} {self.period_end_year}"

    @property
    def differs(self) -> bool:
        """The company's own quarter is not simply the calendar quarter of the same name: the labels differ, or it
        does not end on a calendar quarter boundary (Target's Nov-Jan 'Q4 2025' shares its label but ends in Jan 2026)."""
        return self.calendar_label != self.fiscal_label or self.period_end_month % 3 != 0

    def caption(self) -> str:
        if not self.differs:
            return f"Q{self.fiscal_quarter} {self.fiscal_year}"
        return (f"FY{self.fiscal_year} Q{self.fiscal_quarter} (quarter ended {self.period_end_label}; "
                f"calendar {self.calendar_label})")

    def as_meta(self) -> dict:
        return {"calendar_label": self.calendar_label, "fiscal_label": self.fiscal_label,
                "period_end": f"{self.period_end_year}-{self.period_end_month:02d}", "fye_month": self.fye_month,
                "fy_naming": self.naming, "fiscal_calendar_source": self.source}


# --------------------------------------------------------------------------- #
# Reading a label
# --------------------------------------------------------------------------- #

_Q_YEAR_FIRST = re.compile(r"^\s*(?P<y>(?:19|20)\d\d)\s*[-_ /]?\s*[Qq]\s*(?P<q>[1-4])\s*$")
_Q_QUARTER_FIRST = re.compile(r"^\s*[Qq]\s*(?P<q>[1-4])\s*[-_ /']?\s*(?P<y>(?:19|20)\d\d)\s*$")


def parse_quarter(text: str) -> tuple[int, int]:
    """'2026q2' | '2026Q2' | '2026-Q2' | 'Q2 2026' -> (2026, 2)."""
    m = _Q_YEAR_FIRST.match(text or "") or _Q_QUARTER_FIRST.match(text or "")
    if not m:
        raise ValueError(f"expected a quarter like 2026Q2 (also 2026-q2 or Q2 2026), got {text!r}")
    return int(m["y"]), int(m["q"])


# --------------------------------------------------------------------------- #
# Where a company's fiscal calendar comes from
# --------------------------------------------------------------------------- #

_cache: dict[str, dict] = {}


def load_calendars(path: Path = CALENDARS_FILE) -> dict[str, FiscalCalendar]:
    if not path.exists():
        return {}
    key = str(path)
    if key not in _cache:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _cache[key] = {
            sym.upper(): FiscalCalendar(int(v["fye_month"]), v.get("naming", "end"), v.get("source", "fiscal_calendars.json"))
            for sym, v in raw.get("companies", {}).items()}
    return _cache[key]


def _library_fye_month(symbol: str) -> int | None:
    """Best effort: month of the latest fiscal-year end in the MarketDataLibrary. Silent if the file is missing,
    locked by another process, or shaped differently than expected."""
    if not LIBRARY_FILE.exists():
        return None
    try:
        import duckdb
        con = duckdb.connect(str(LIBRARY_FILE), read_only=True)
        try:
            row = con.execute("SELECT MAX(period_ending) FROM financials WHERE symbol = ? AND period_label LIKE 'FY%'",
                              [symbol]).fetchone()
        finally:
            con.close()
        d = row[0] if row else None
        if d is None:
            return None
        if not hasattr(d, "month"):
            d = date.fromisoformat(str(d)[:10])
    except Exception:
        return None
    return (d.month - 1 or 12) if d.day <= 7 else d.month          # "Jan 3" is really the December year-end


def fiscal_calendar(symbol: str, calendars: dict[str, FiscalCalendar] | None = None,
                    use_library: bool = True) -> FiscalCalendar:
    symbol = symbol.strip().upper()
    known = (calendars if calendars is not None else load_calendars()).get(symbol)
    if known:
        return known
    if use_library:
        month = _library_fye_month(symbol)
        if month:
            return FiscalCalendar(month, "end", "MarketDataLibrary financials.period_ending (naming assumed: end)")
    return FiscalCalendar(12, "end", ASSUMED)


# --------------------------------------------------------------------------- #
# The mapping
# --------------------------------------------------------------------------- #

def _fy_label(fy_end_year: int, cal: FiscalCalendar) -> int:
    if cal.naming == "start" and cal.fye_month != 12:
        return fy_end_year - 1
    return fy_end_year


def _from_end_index(symbol: str, cal: FiscalCalendar, end: int) -> CompanyQuarter:
    """`end` = months since year 0 (year * 12 + month - 1) of the company quarter's last month. Everything else follows:
    the fiscal year-end that closes its year, the quarter number, and the calendar quarter of its MIDDLE month."""
    m = cal.fye_month
    end_year, end_month = end // 12, end % 12 + 1
    fy_end_year = end_year if end_month <= m else end_year + 1
    months_to_year_end = (fy_end_year * 12 + m - 1) - end                        # 0, 3, 6 or 9
    middle = end - 1
    return CompanyQuarter(symbol, middle // 12, (middle % 12) // 3 + 1, _fy_label(fy_end_year, cal),
                          4 - months_to_year_end // 3, end_year, end_month, m, cal.naming, cal.source)


def _from_calendar(symbol: str, cal: FiscalCalendar, year: int, quarter: int) -> CompanyQuarter:
    """The company quarter whose middle month lies in the calendar quarter: its last month is one of the three months
    after the calendar quarter's first month, and it must be a fiscal quarter-end month (congruent to m mod 3)."""
    first = year * 12 + 3 * (quarter - 1)
    end = next(e for e in range(first + 1, first + 4) if (e - (cal.fye_month - 1)) % 3 == 0)
    return _from_end_index(symbol, cal, end)


def _from_fiscal(symbol: str, cal: FiscalCalendar, fy: int, fq: int) -> CompanyQuarter:
    m = cal.fye_month
    fy_end_year = fy + 1 if (cal.naming == "start" and m != 12) else fy
    return _from_end_index(symbol, cal, fy_end_year * 12 + (m - 1) - 3 * (4 - fq))


def resolve(symbol: str, text: str, calendars: dict[str, FiscalCalendar] | None = None,
            use_library: bool = True) -> CompanyQuarter:
    """A CALENDAR quarter ('2026q2') -> the company's own quarter for it."""
    year, quarter = parse_quarter(text)
    symbol = symbol.strip().upper()
    return _from_calendar(symbol, fiscal_calendar(symbol, calendars, use_library), year, quarter)


def from_fiscal(symbol: str, text: str, calendars: dict[str, FiscalCalendar] | None = None,
                use_library: bool = True) -> CompanyQuarter:
    """The company's own fiscal label ('2026Q2' as Alpha Vantage uses it) -> the same quarter, with its calendar quarter."""
    year, quarter = parse_quarter(text)
    symbol = symbol.strip().upper()
    return _from_fiscal(symbol, fiscal_calendar(symbol, calendars, use_library), year, quarter)


def attach_period(meta: dict, calendars: dict[str, FiscalCalendar] | None = None,
                  use_library: bool = True) -> CompanyQuarter | None:
    """Fill calendar_label / period_end / ... into a transcript's meta from its ticker + fiscal year + fiscal quarter."""
    if not (meta.get("ticker") and meta.get("fiscal_year") and meta.get("fiscal_quarter")):
        return None
    cal = fiscal_calendar(meta["ticker"], calendars, use_library)
    cq = _from_fiscal(str(meta["ticker"]).upper(), cal, int(meta["fiscal_year"]), int(meta["fiscal_quarter"]))
    for k, v in cq.as_meta().items():
        meta.setdefault(k, v)
    return cq


def title_suffix(meta: dict) -> str:
    """' (calendar 2026Q1; quarter ended Jan 2026)' when the company's own label differs from the calendar one."""
    cal, fy, fq = meta.get("calendar_label"), meta.get("fiscal_year"), meta.get("fiscal_quarter")
    end = meta.get("period_end")
    if not (cal and fy and fq) or (cal == f"{fy}Q{fq}" and not (end and int(end[5:7]) % 3)):
        return ""
    ended = f"; quarter ended {_MONTH[int(end[5:7]) - 1]} {end[:4]}" if end else ""
    return f" (calendar {cal}{ended})"


# --------------------------------------------------------------------------- #
# Does the call agree with the label it was filed under?
# --------------------------------------------------------------------------- #

_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_CALL_WORDS = re.compile(
    r"\b(?P<w>first|second|third|fourth)\s+quarter(?:\s+(?:of\s+)?(?:(?:fiscal|calendar)\s+(?:year\s+)?)?(?P<y>20\d\d))?", re.I)
_CALL_TAG = re.compile(r"\bQ(?P<q>[1-4])(?:\s*(?:FY)?\s*'?(?P<y>(?:20)?\d\d))?\b")


def check_call_period(text: str, cq: CompanyQuarter, head: int = 6000) -> dict:
    """Compare the quarter the call names for itself (first mention near the top) with the one we expect.
    A mismatch usually means the fiscal calendar on file for the symbol is wrong."""
    top = text[:head]
    hits = []
    for m in _CALL_WORDS.finditer(top):
        hits.append((m.start(), _ORDINAL[m["w"].lower()], int(m["y"]) if m["y"] else None, m.group(0)))
    for m in _CALL_TAG.finditer(top):
        y = m["y"]
        hits.append((m.start(), int(m["q"]), (int(y) if len(y) == 4 else 2000 + int(y)) if y else None, m.group(0)))
    if not hits:
        return {"status": "unknown", "found": None, "detail": "the call does not name its quarter near the start"}
    _, quarter, year, found = min(hits)
    where = f"expected {cq.fiscal_label} (FY{cq.fiscal_year} Q{cq.fiscal_quarter}, quarter ended {cq.period_end_label})"
    if quarter != cq.fiscal_quarter:
        return {"status": "warn", "found": found, "detail":
                f"the call says {found!r} but {where}; check the fiscal calendar for {cq.symbol} ({cq.source})"}
    if year is not None and year != cq.fiscal_year:
        return {"status": "warn", "found": found, "detail":
                f"the call says {found!r} but {where}; the fiscal-year naming for {cq.symbol} may be wrong"}
    return {"status": "pass", "found": found, "detail": f"the call says {found!r}, matching {cq.fiscal_label}"}


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #

def _latest_calendar_quarter(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    q = (today.month - 1) // 3 + 1
    return (today.year, q - 1) if q > 1 else (today.year - 1, 4)


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    symbol = argv[0].upper()
    cal = fiscal_calendar(symbol)
    print(f"{symbol}: fiscal year ends in {_MONTH[cal.fye_month - 1]}, year named by its {cal.naming}; source: {cal.source}")
    if len(argv) > 1:
        cq = resolve(symbol, argv[1])
        print(f"  calendar {cq.calendar_label} -> {cq.caption()}\n  send Alpha Vantage: --av {symbol} {cq.fiscal_label} "
              f"(or simply: py main.py --av {symbol} {cq.calendar_label})")
        return 0
    year, quarter = _latest_calendar_quarter()
    for _ in range(8):
        cq = resolve(symbol, f"{year}Q{quarter}")
        print(f"  calendar {cq.calendar_label}  ->  {cq.caption():<62} Alpha Vantage: {cq.fiscal_label}")
        year, quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

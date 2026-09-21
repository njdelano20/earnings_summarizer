"""
fetchers.py
Responsible for sourcing raw transcripts.

Two sources, both returning the same kind of dict:
  fetch_from_folder()        -> {"filename", "text"}                (plain .txt files)
  fetch_from_alphavantage()  -> {"filename", "turns", "source_meta"} (speaker-tagged JSON turns)

Alpha Vantage notes (EARNINGS_CALL_TRANSCRIPT):
  * `quarter` is formatted YYYYQn, e.g. "2024Q1".
  * Errors, bad keys and rate limits come back as HTTP 200 with an
    {"Information"|"Note"|"Error Message": "..."} body, so the body must be checked.
  * Raw responses are cached under data/raw/alphavantage/ so a repeat run costs no quota.
  * The API key is read from the ALPHAVANTAGE_API_KEY environment variable only.
"""

import json
import os
import re
from pathlib import Path

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
_QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")


class AlphaVantageError(RuntimeError):
    """The API answered, but with an error / rate-limit / bad-key message."""


class NoTranscriptError(AlphaVantageError):
    """The API answered normally but has no transcript for that symbol/quarter."""


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def fetch_from_folder(folder_path: str) -> list[dict]:
    """
    Reads all .txt files in the given folder and returns a list of dicts:
    [{"filename": "AAPL_Q3_2026.txt", "text": "..."}]
    """
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Transcript folder not found: {folder_path}")

    return [{"filename": p.name, "text": _read_text(p)} for p in sorted(folder.glob("*.txt"))]


def fetch_from_alphavantage(symbol: str, quarter: str, cache_dir: str | Path,
                            api_key: str | None = None, use_demo_key: bool = False,
                            refresh: bool = False) -> dict:
    """
    Returns {"filename": "IBM_2024Q1.json", "turns": [...], "source_meta": {...}}.
    Uses the on-disk cache unless refresh=True. Raises AlphaVantageError / NoTranscriptError.
    """
    symbol = symbol.strip().upper()
    quarter = quarter.strip().upper()
    if not _QUARTER_RE.match(quarter):
        raise ValueError(f"quarter must look like 2024Q1, got {quarter!r}")

    cache_path = Path(cache_dir) / f"{symbol}_{quarter}.json"
    from_cache = cache_path.exists() and not refresh
    if from_cache:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        key = "demo" if use_demo_key else (api_key or os.environ.get("ALPHAVANTAGE_API_KEY"))
        if not key:
            raise AlphaVantageError(
                "No API key. Set the ALPHAVANTAGE_API_KEY environment variable "
                "(free key: alphavantage.co/support/#api-key) or use the demo key (IBM 2024Q1 only)."
            )
        payload = _request(symbol, quarter, key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "filename": f"{symbol}_{quarter}.json",
        "turns": payload["transcript"],
        "source_meta": {"quarter_label": quarter, "symbol": symbol,
                        "from_cache": from_cache, "provider": "alphavantage"},
    }


_KEY_LIKE = re.compile(r"(?i)(api\s*key(?:\s+as|\s*[=:])?\s*)([A-Za-z0-9]{10,})")


def scrub_secrets(text: str, key: str | None = None) -> str:
    """Remove the API key from any text before it can reach a log, a file or a console. Two layers: the exact key if
    we know it, and anything shaped like 'API key as XXXX' / 'apikey=XXXX' (Alpha Vantage's own rate-limit reply
    quotes the key back, so a message from the API is as dangerous as a network error)."""
    if key:
        text = text.replace(key, "***")
    return _KEY_LIKE.sub(r"\1***", text)


def _request(symbol: str, quarter: str, api_key: str) -> dict:
    import requests

    # The key travels as a URL parameter, so a requests exception message contains it. Never let it reach a log:
    # re-raise everything as AlphaVantageError with the key scrubbed and the original chained away.
    def scrub(text: str) -> str:
        return scrub_secrets(text, api_key)

    try:
        resp = requests.get(
            ALPHAVANTAGE_URL,
            params={"function": "EARNINGS_CALL_TRANSCRIPT", "symbol": symbol,
                    "quarter": quarter, "apikey": api_key},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AlphaVantageError(f"Alpha Vantage request failed ({type(exc).__name__}): {scrub(str(exc))}") from None
    try:
        data = resp.json()
    except ValueError:
        raise AlphaVantageError(f"Non-JSON response from Alpha Vantage: {scrub(resp.text[:200])!r}") from None

    for msg_key in ("Information", "Note", "Error Message"):
        if msg_key in data:
            raise AlphaVantageError(scrub(f"Alpha Vantage: {data[msg_key]}"))     # the API echoes the key in its replies
    turns = data.get("transcript")
    if not isinstance(turns, list) or not turns:
        raise NoTranscriptError(f"No transcript returned for {symbol} {quarter}.")
    return data

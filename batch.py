"""
batch.py
Run one calendar quarter across many symbols, unattended, and measure how well the pipeline did.

    py batch.py --season 2026q2 --symbols CPRT,VICI,HESM          a few tickers
    py batch.py --season 2026q2 --symbols-file watchlist.txt       one ticker per line (# comments allowed)
    py batch.py --season 2026q2 --from-securities --limit 25       the first 25 common stocks in the MarketDataLibrary
    py batch.py --season 2026q2 --symbols CPRT --dry-run           show what would happen; fetch and write nothing
    py batch.py --season 2026q2 --symbols CPRT --cache-only        never touch the network (rescoring after code changes)
    py batch.py --audit-report                                     score the spot-check verdicts you have filled in

"2026q2" is the calendar quarter (see periods.py): each symbol is resolved to its own fiscal quarter first.

Safe to run again and again (Windows Task Scheduler is fine): anything already done is skipped, a transcript that is
not out yet is retried later, a rate limit stops the run cleanly, and one bad symbol never stops the others. At most
--max-fetches new transcripts are requested per run (default 20, under the free tier's ~25 a day); transcripts already
in data/raw/alphavantage/ cost nothing.

Everything lands in output/batch/<season>/ :
    calls/<CALL>_facts.json, _snapshot.md/.json, _score.json      the pipeline's outputs for each call
    scorecard.csv / .json      one row per symbol: how many facts, how many verified, how many figures went unclaimed,
                               period check, checks, gold result if a gold file exists, and `db_eligible`
    review_queue.csv           every non-high-confidence fact and every figure the extractor could not attach to a metric,
                               each with its source sentence: the to-do list for a human (or a short Claude session)
    audit_sample.csv           a few random high-confidence facts per call; fill in the `verdict` column (ok / wrong)
    state.json, summary.md     per-symbol status (ok / no_transcript / rate_limited / error) and a readable summary
plus output/batch/scorecard_all.csv (every season) and batch.log.

THIS SCRIPT NEVER WRITES TO THE DATABASE. `db_eligible` is only a recommendation for a future, separate loader.
The scorecard needs no gold file: it counts what the pipeline can verify about itself. It cannot tell that a figure was
attached to the wrong subject, which is what the audit sample is for (`--audit-report` turns your verdicts into an
error rate with a 95% upper bound).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import periods
from evaluate import evaluate, evaluate_snapshot, facts_ok, snapshot_ok
from facts import extract_facts
from fetchers import AlphaVantageError, NoTranscriptError, fetch_from_alphavantage
from snapshot import build_snapshot
from transcript import build_from_structured
from writer import write_facts_json, write_snapshot

ROOT = Path(__file__).resolve().parent
AV_CACHE = ROOT / "data" / "raw" / "alphavantage"
BATCH_ROOT = ROOT / "output" / "batch"
GOLD = ROOT / "gold"
SECURITIES_CSV = periods.LIBRARY_FILE.parent / "securities.csv"

SCORE_COLUMNS = [
    "symbol", "season", "status", "detail", "fiscal_label", "period_end", "fiscal_calendar", "source",
    "words", "facts", "high", "medium", "low", "unverified", "verified_pct", "unclaimed_figures", "claimed_share",
    "conflicts", "checks_failed", "checks_warn", "period_check", "signals", "selected", "trajectory_rows",
    "snapshot_checks_failed", "snapshot_checks_warn", "registry", "unmapped_segments", "gold_facts", "gold_snapshot",
    "db_eligible", "attention"]
REVIEW_COLUMNS = ["symbol", "season", "kind", "fact_id", "metric", "segment", "stat", "value", "period", "confidence",
                  "flags_or_reason", "section", "speaker", "sentence"]
AUDIT_COLUMNS = ["symbol", "season", "fact_id", "metric", "segment", "stat", "value", "period", "confidence", "section",
                 "speaker", "sentence", "verdict", "note"]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def log(message: str, root: Path | None = None) -> None:
    print(message)
    root = root or BATCH_ROOT
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / "batch.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')}  {message}\n")
    except OSError:
        pass


def season_label(text: str) -> str:
    year, quarter = periods.parse_quarter(text)
    return f"{year}Q{quarter}"


def load_symbols(symbols: str | None = None, symbols_file: str | None = None, from_securities: bool = False,
                 offset: int = 0, limit: int | None = None, securities_csv: Path | None = None) -> list[str]:
    out: list[str] = []
    if symbols:
        out += [s for s in re.split(r"[,\s]+", symbols) if s]
    if symbols_file:
        for line in Path(symbols_file).read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            out += [s for s in re.split(r"[,\s]+", line) if s]
    if from_securities:
        path = securities_csv or SECURITIES_CSV
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = [r["symbol"] for r in csv.DictReader(fh) if r.get("type") == "Common Stock"]
        out += rows[offset:offset + limit if limit else None]
    seen, unique = set(), []
    for s in out:
        s = s.strip().upper()
        if s and s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def classify_av_error(message: str) -> str:
    """'rate_limited' (stop for today), 'fatal' (bad or missing key: stop), or 'error'."""
    m = message.lower()
    if any(k in m for k in ("no api key", "apikey", "api key", "invalid api")):
        return "fatal"
    if any(k in m for k in ("rate limit", "requests per day", "call frequency", "per minute", "premium", "daily")):
        return "rate_limited"
    return "error"


def decide(entry: dict, cached: bool, fetches_left: int, today: date, retry_after_days: int = 2,
           rescore: bool = False) -> tuple[str, str]:
    """('process' | 'fetch' | 'skip', reason) for one symbol."""
    status = entry.get("status")
    if cached:
        if status == "ok" and not rescore:
            return "skip", "done (use --rescore to run it again from the cache)"
        return "process", "cached"
    last = entry.get("last_attempt")
    age = (today - date.fromisoformat(last)).days if last else None
    if status in ("no_transcript", "error") and age is not None and age < retry_after_days:
        return "skip", f"{status} {age} day(s) ago; not asking again for {retry_after_days} days"
    if status == "rate_limited" and age == 0:
        return "skip", "rate-limited earlier today"
    if fetches_left <= 0:
        return "skip", "fetch budget for this run is used up"
    return "fetch", "not cached"


def upper_error_bound(n: int, k: int, confidence: float = 0.95) -> float:
    """One-sided upper confidence bound on an error rate after finding k wrong in n checked (exact binomial).
    k=0, n=100 -> about 3%: 'if 100 random facts are checked and none is wrong, the true rate is probably under 3%'."""
    if n <= 0:
        return 1.0
    alpha = 1.0 - confidence

    def cdf(p: float) -> float:                     # P(X <= k) for X ~ Binomial(n, p); falls as p rises
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))

    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


# --------------------------------------------------------------------------- #
# describing facts for humans
# --------------------------------------------------------------------------- #

def _amount(v, unit: str) -> str:
    if v is None:
        return ""
    if unit == "USD":
        for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(v) >= div:
                return f"${v / div:g}{suffix}"
        return f"${v:g}"
    return {"USD_per_share": f"${v:g}/sh", "pct": f"{v:g}%", "bps": f"{v:g} bps", "pp": f"{v:g} pp"}.get(unit, f"{v:g} {unit}")


def fact_value_text(f: dict) -> str:
    if f.get("value") is None:
        text = f.get("descriptor") or f.get("figure_text") or ""
    else:
        text = _amount(f["value"], f["unit"])
        if f.get("value_high") is not None:
            text += f" to {_amount(f['value_high'], f['unit'])}"
        if f.get("qualifier"):
            text = f"{f['qualifier']} {text}"
        if f.get("descriptor"):
            text += f" ({f['descriptor']})"
    changes = ", ".join(f"{_amount(c['value'], c['unit'])}{'/' + c['basis'] if c.get('basis') else ''}"
                        for c in f.get("changes", []))
    return f"{text} [{changes}]" if changes else text


def review_rows(symbol: str, season: str, result: dict) -> list[dict]:
    rows = []
    for f in result["facts"]:
        if f["confidence"] == "high" and f["verified"]:
            continue
        rows.append({"symbol": symbol, "season": season, "kind": f"{f['confidence']}_confidence_fact" if f["verified"]
                     else "unverified_fact", "fact_id": f["id"], "metric": f["metric"], "segment": f["segment"] or "",
                     "stat": f["stat"], "value": fact_value_text(f), "period": f["period"] or "", "confidence": f["confidence"],
                     "flags_or_reason": "; ".join(f["flags"]), "section": f["section"], "speaker": f["speaker"],
                     "sentence": f["sentence"][:400]})
    for u in result["unclaimed"]:
        rows.append({"symbol": symbol, "season": season, "kind": "unclaimed_figure", "fact_id": "", "metric": "",
                     "segment": "", "stat": "", "value": u["figure_text"], "period": "", "confidence": "",
                     "flags_or_reason": u["reason"], "section": u["section"], "speaker": u["speaker"],
                     "sentence": u["sentence"][:400]})
    return rows


def audit_rows(symbol: str, season: str, result: dict, per_call: int) -> list[dict]:
    """A reproducible random sample of high-confidence, verified facts for a human to check against the transcript."""
    pool = [f for f in result["facts"] if f["confidence"] == "high" and f["verified"] and f["kind"] != "declared"]
    rng = random.Random(f"{symbol}|{season}")
    picked = rng.sample(pool, min(per_call, len(pool)))
    return [{"symbol": symbol, "season": season, "fact_id": f["id"], "metric": f["metric"], "segment": f["segment"] or "",
             "stat": f["stat"], "value": fact_value_text(f), "period": f["period"] or "", "confidence": f["confidence"],
             "section": f["section"], "speaker": f["speaker"], "sentence": f["sentence"][:400], "verdict": "", "note": ""}
            for f in picked]


# --------------------------------------------------------------------------- #
# scoring one call
# --------------------------------------------------------------------------- #

def gold_status(stem: str) -> tuple[str, str]:
    """('none' | 'pass' | 'fail' | 'error', same) for the facts gold and the snapshot gold of this call, if any."""
    out = []
    for path, fn, ok in ((GOLD / f"{stem}.json", evaluate, facts_ok),
                         (GOLD / "snapshot" / f"{stem}.json", evaluate_snapshot, snapshot_ok)):
        if not path.exists():
            out.append("none")
            continue
        try:
            out.append("pass" if ok(fn(path)) else "fail")
        except Exception:
            out.append("error")
    return out[0], out[1]


def attention_flags(row: dict) -> list[str]:
    flags = []
    if row["period_check"] == "warn":
        flags.append("period_mismatch")
    if row["period_check"] == "unknown":
        flags.append("period_unchecked")
    if row["unverified"]:
        flags.append("unverified_facts")
    if row["checks_failed"] or row["snapshot_checks_failed"]:
        flags.append("checks_failed")
    if row["facts"] < 5:
        flags.append("few_facts")
    if row["claimed_share"] != "" and row["claimed_share"] < 0.4:
        flags.append("many_unclaimed_figures")
    if row["registry"] == "auto":
        flags.append("no_segment_map")
    if row["fiscal_calendar"] == "assumed":
        flags.append("assumed_calendar_year")
    if "fail" in (row["gold_facts"], row["gold_snapshot"]):
        flags.append("gold_fail")
    return flags


def score_call(symbol: str, season: str, cq, item: dict, transcript, result: dict, snapshot: dict, check: dict) -> dict:
    stats, sstats = result["stats"], snapshot["stats"]
    by_conf = stats["by_confidence"]
    n, unclaimed = stats["facts"], stats["unclaimed_figures"]
    stem = Path(item["filename"]).stem
    gold_f, gold_s = gold_status(stem)
    row = {
        "symbol": symbol, "season": season, "status": "ok", "detail": "", "fiscal_label": cq.fiscal_label,
        "period_end": f"{cq.period_end_year}-{cq.period_end_month:02d}",
        "fiscal_calendar": "assumed" if cq.source == periods.ASSUMED else ("verified" if cq.source.startswith("verified")
                                                                          else "seed/library"),
        "source": "cache" if item["source_meta"]["from_cache"] else "alphavantage",
        "words": len(transcript.text.split()), "facts": n, "high": by_conf.get("high", 0), "medium": by_conf.get("medium", 0),
        "low": by_conf.get("low", 0), "unverified": stats["unverified"],
        "verified_pct": round(100 * stats["verified"] / n, 1) if n else "", "unclaimed_figures": unclaimed,
        "claimed_share": round(n / (n + unclaimed), 3) if (n + unclaimed) else "",
        "conflicts": sum(1 for f in result["facts"] if "conflict_with_other_value" in f["flags"]),
        "checks_failed": stats["checks_failed"], "checks_warn": stats["checks_warn"], "period_check": check["status"],
        "signals": sstats["signals"], "selected": sstats["selected"], "trajectory_rows": sstats["trajectory_rows"],
        "snapshot_checks_failed": sstats["checks_failed"], "snapshot_checks_warn": sstats["checks_warn"],
        "registry": "curated" if snapshot["coverage"]["registry"] == "curated" else "auto",
        "unmapped_segments": len(snapshot["coverage"]["unmapped_fact_segments"]),
        "gold_facts": gold_f, "gold_snapshot": gold_s,
    }
    row["db_eligible"] = bool(check["status"] == "pass" and row["unverified"] == 0 and row["checks_failed"] == 0
                              and row["snapshot_checks_failed"] == 0 and "fail" not in (gold_f, gold_s) and n > 0)
    row["attention"] = "; ".join(attention_flags(row))
    return row


def blank_row(symbol: str, season: str, entry: dict) -> dict:
    row = {c: "" for c in SCORE_COLUMNS}
    row.update({"symbol": symbol, "season": season, "status": entry.get("status", "pending"),
                "detail": entry.get("detail", ""), "db_eligible": False})
    return row


# --------------------------------------------------------------------------- #
# state and outputs
# --------------------------------------------------------------------------- #

def load_state(folder: Path) -> dict:
    path = folder / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"symbols": {}}


def save_state(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def append_audit(folder: Path, symbol: str, season: str, result: dict, per_call: int) -> None:
    path = folder / "audit_sample.csv"
    existing = _read_csv(path)
    if any(r["symbol"] == symbol for r in existing) or per_call <= 0:
        return                                   # sampled once per call, so verdicts you have entered are never lost
    _write_csv(path, AUDIT_COLUMNS, existing + audit_rows(symbol, season, result, per_call))


def rebuild_season(folder: Path, season: str, state: dict) -> tuple[list[dict], list[dict]]:
    """Regenerate scorecard / review queue / summary from the per-call files, so partial runs never leave them stale."""
    score_rows, queue = [], []
    for symbol, entry in sorted(state["symbols"].items()):
        stem = entry.get("stem")
        score_path = folder / "calls" / f"{stem}_score.json" if stem else None
        if entry.get("status") == "ok" and score_path and score_path.exists():
            score_rows.append(json.loads(score_path.read_text(encoding="utf-8")))
            facts_path = folder / "calls" / f"{stem}_facts.json"
            if facts_path.exists():
                queue += review_rows(symbol, season, json.loads(facts_path.read_text(encoding="utf-8")))
        else:
            score_rows.append(blank_row(symbol, season, entry))
    folder.mkdir(parents=True, exist_ok=True)
    _write_csv(folder / "scorecard.csv", SCORE_COLUMNS, score_rows)
    (folder / "scorecard.json").write_text(json.dumps(score_rows, indent=1, ensure_ascii=False), encoding="utf-8")
    _write_csv(folder / "review_queue.csv", REVIEW_COLUMNS, queue)
    _write_summary(folder, season, score_rows, len(queue))
    return score_rows, queue


def rebuild_all(batch_root: Path) -> None:
    rows = []
    for path in sorted(batch_root.glob("*/scorecard.json")):
        rows += json.loads(path.read_text(encoding="utf-8"))
    if rows:
        _write_csv(batch_root / "scorecard_all.csv", SCORE_COLUMNS, rows)


def _write_summary(folder: Path, season: str, rows: list[dict], queue_len: int) -> None:
    from collections import Counter
    status = Counter(r["status"] for r in rows)
    ok = [r for r in rows if r["status"] == "ok"]
    lines = [f"# Batch {season}", "", f"Updated {datetime.now().isoformat(timespec='minutes')}. "
             f"{len(rows)} symbols: " + ", ".join(f"{n} {s}" for s, n in status.most_common()) + ".", ""]
    if ok:
        eligible = sum(1 for r in ok if r["db_eligible"])
        lines += [f"- Calls processed: {len(ok)}; `db_eligible` (recommendation only, nothing is loaded anywhere): {eligible}",
                  f"- Facts: {sum(r['facts'] for r in ok)} (high {sum(r['high'] for r in ok)}, medium {sum(r['medium'] for r in ok)}, "
                  f"low {sum(r['low'] for r in ok)}); figures left unclaimed: {sum(r['unclaimed_figures'] for r in ok)}",
                  f"- Review queue: {queue_len} rows in review_queue.csv", ""]
        flagged = [r for r in ok if r["attention"]]
        if flagged:
            lines += ["## Needs attention", "", "| Symbol | Fiscal | Facts | Unclaimed | Flags |", "|---|---|---|---|---|"]
            lines += [f"| {r['symbol']} | {r['fiscal_label']} | {r['facts']} | {r['unclaimed_figures']} | {r['attention']} |"
                      for r in flagged]
            lines.append("")
    waiting = [r for r in rows if r["status"] != "ok"]
    if waiting:
        lines += ["## Not done", "", "| Symbol | Status | Detail |", "|---|---|---|"]
        lines += [f"| {r['symbol']} | {r['status']} | {r['detail']} |" for r in waiting]
        lines.append("")
    lines += ["## Spot-check", "", "Open audit_sample.csv and compare each fact with its sentence. Check the LABELS as well as the "
              "number: is it the right segment (not the whole company), the right period, reported vs guidance, and the right "
              "basis (year-over-year vs sequential)? Most real errors are a correct number with a wrong label. Type `ok` or "
              "`wrong` in the `verdict` column, then run `py batch.py --audit-report`.", ""]
    (folder / "summary.md").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #

def run_call(symbol: str, season: str, folder: Path, audit_per_call: int) -> dict:
    """Fetch (or read from the cache), extract, snapshot, write, score. Raises on any problem."""
    cq = periods.resolve(symbol, season)
    item = fetch_from_alphavantage(symbol, cq.fiscal_label, AV_CACHE)
    transcript = build_from_structured(item["turns"], item["filename"], {**item["source_meta"], **cq.as_meta()})
    check = periods.check_call_period(transcript.text, cq)
    transcript.meta["period_check"] = check
    result = extract_facts(transcript)
    snapshot = build_snapshot(transcript, result)
    calls = folder / "calls"
    write_facts_json(item["filename"], result, str(calls))
    write_snapshot(item["filename"], snapshot, result, str(calls))
    import annotate                       # imported here because annotate imports helpers from this module
    annotate.write_page(transcript, result, calls)          # the readable, UNCHECKED page for this call
    row = score_call(symbol, season, cq, item, transcript, result, snapshot, check)
    stem = Path(item["filename"]).stem
    (calls / f"{stem}_score.json").write_text(json.dumps(row, indent=1, ensure_ascii=False), encoding="utf-8")
    append_audit(folder, symbol, season, result, audit_per_call)
    row["_stem"] = stem
    return row


def run(symbols: list[str], season: str, max_fetches: int = 20, dry_run: bool = False, rescore: bool = False,
        retry_after_days: int = 2, audit_per_call: int = 3, pause: float = 1.2, today: date | None = None,
        batch_root: Path | None = None) -> int:
    batch_root = batch_root or BATCH_ROOT
    season = season_label(season)
    folder = batch_root / season
    today = today or date.today()
    state = load_state(folder)
    fetches_left, fetched, fatal = max_fetches, 0, None
    counts: dict[str, int] = {}

    for symbol in symbols:
        entry = state["symbols"].get(symbol, {})
        try:
            cq = periods.resolve(symbol, season)
        except ValueError as exc:
            log(f"{symbol}: {exc}", batch_root)
            continue
        cached = (AV_CACHE / f"{symbol}_{cq.fiscal_label}.json").exists()
        action, reason = decide(entry, cached, fetches_left, today, retry_after_days, rescore)
        counts[action] = counts.get(action, 0) + 1
        if dry_run:
            print(f"{symbol:<7} {season} -> Alpha Vantage {cq.fiscal_label}  ({cq.caption()})  "
                  f"cached={'yes' if cached else 'no'}  last={entry.get('status', '-')}  => {action}: {reason}")
            continue
        if action == "skip":
            log(f"{symbol}: skipped, {reason}", batch_root)
            continue
        if fatal and action == "fetch":                  # cached calls cost no quota, so they still run
            log(f"{symbol}: not attempted ({fatal})", batch_root)
            continue

        entry = dict(entry, attempts=entry.get("attempts", 0) + (0 if cached else 1), last_attempt=today.isoformat(),
                     fiscal_label=cq.fiscal_label)
        if action == "fetch":
            fetches_left -= 1
            fetched += 1
            if fetched > 1 and pause:
                time.sleep(pause)
        try:
            row = run_call(symbol, season, folder, audit_per_call)
            entry.update(status="ok", detail="", stem=row.pop("_stem"), processed=datetime.now().isoformat(timespec="seconds"))
            log(f"{symbol}: ok ({'cache' if cached else 'fetched'}) {cq.fiscal_label}: {row['facts']} facts, "
                f"{row['unclaimed_figures']} unclaimed, period {row['period_check']}"
                f"{', ATTENTION: ' + row['attention'] if row['attention'] else ''}", batch_root)
        except NoTranscriptError as exc:
            entry.update(status="no_transcript", detail=str(exc))
            log(f"{symbol}: no transcript for {cq.fiscal_label} yet ({exc})", batch_root)
        except AlphaVantageError as exc:
            kind = classify_av_error(str(exc))
            entry.update(status="rate_limited" if kind == "rate_limited" else "error", detail=str(exc)[:300])
            log(f"{symbol}: {kind}: {str(exc)[:200]}", batch_root)
            if kind in ("rate_limited", "fatal"):
                fatal = "rate limit reached; run again later" if kind == "rate_limited" else "fix the API key, then run again"
        except Exception as exc:                                  # one bad symbol must not stop the others
            entry.update(status="error", detail=f"{type(exc).__name__}: {str(exc)[:250]}")
            log(f"{symbol}: error {type(exc).__name__}: {str(exc)[:200]}", batch_root)
        state["symbols"][symbol] = entry
        save_state(folder, state)

    if dry_run:
        print(f"\nDry run: {counts.get('fetch', 0)} would be fetched (budget {max_fetches}), {counts.get('process', 0)} "
              f"processed from cache, {counts.get('skip', 0)} skipped. Nothing was fetched or written.")
        return 0
    rows, queue = rebuild_season(folder, season, state)
    rebuild_all(batch_root)
    ok = [r for r in rows if r["status"] == "ok"]
    log(f"Done: {len(ok)} of {len(rows)} symbols scored ({fetched} fetched this run); review queue {len(queue)} rows; "
        f"see {folder / 'summary.md'}", batch_root)
    if fatal:
        log(f"Stopped early: {fatal}.", batch_root)
    return 1 if fatal and "API key" in fatal else 0


def audit_report(paths: list[Path] | None = None) -> int:
    paths = paths or sorted(BATCH_ROOT.glob("*/audit_sample.csv"))
    rows = [r for p in paths for r in _read_csv(p)]
    good = {"ok", "y", "yes", "correct", "right", "true", "1"}
    bad = {"wrong", "n", "no", "incorrect", "false", "0", "x"}
    checked = [(r, r["verdict"].strip().lower()) for r in rows if r["verdict"].strip().lower() in good | bad]
    n, k = len(checked), sum(1 for _, v in checked if v in bad)
    print(f"{len(rows)} facts in the audit sample(s); {n} checked; {len(rows) - n} still blank.")
    if not n:
        print("Fill in the `verdict` column (ok / wrong) first.")
        return 0
    bound = upper_error_bound(n, k)
    print(f"Wrong: {k} of {n} ({100 * k / n:.1f}%). With 95% confidence the true error rate among high-confidence facts "
          f"is at most {100 * bound:.1f}%.")
    for r, v in checked:
        if v in bad:
            print(f"  WRONG {r['symbol']} {r['season']} {r['fact_id']} {r['metric']}/{r['segment']} = {r['value']}: "
                  f"{r['sentence'][:110]} {('| ' + r['note']) if r['note'] else ''}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a calendar quarter across many symbols and score the results")
    ap.add_argument("--season", help="calendar quarter, e.g. 2026q2")
    ap.add_argument("--symbols", help="comma or space separated tickers")
    ap.add_argument("--symbols-file", help="text file, one ticker per line")
    ap.add_argument("--from-securities", action="store_true", help="take common stocks from the MarketDataLibrary securities.csv")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-fetches", type=int, default=20, help="most new transcripts to request per run (default 20)")
    ap.add_argument("--cache-only", action="store_true", help="never use the network")
    ap.add_argument("--dry-run", action="store_true", help="show the plan; fetch and write nothing")
    ap.add_argument("--rescore", action="store_true", help="run already-finished calls again from the cache")
    ap.add_argument("--retry-after-days", type=int, default=2)
    ap.add_argument("--audit-per-call", type=int, default=3)
    ap.add_argument("--pause", type=float, default=1.2, help="seconds between network requests")
    ap.add_argument("--audit-report", nargs="*", metavar="CSV", help="score filled-in audit_sample.csv files (default: all)")
    args = ap.parse_args()

    if args.audit_report is not None:
        return audit_report([Path(p) for p in args.audit_report] or None)
    if not args.season:
        ap.error("--season is required (e.g. --season 2026q2)")
    try:
        symbols = load_symbols(args.symbols, args.symbols_file, args.from_securities, args.offset, args.limit)
    except OSError as exc:
        ap.error(str(exc))
    if not symbols:
        ap.error("no symbols: use --symbols, --symbols-file or --from-securities")
    return run(symbols, args.season, 0 if args.cache_only else args.max_fetches, args.dry_run, args.rescore,
               args.retry_after_days, args.audit_per_call, args.pause)


if __name__ == "__main__":
    sys.exit(main())

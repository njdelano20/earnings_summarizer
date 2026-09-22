"""
draft_gold.py
Drafts a facts gold file for one call using a free LLM (Google Gemini), reading the transcript alone -- the same
blind discipline as the hand-written gold files in gold/*.json: the model never sees what the extractor produced.

A draft is NOT trusted gold. It goes to gold/drafts/<CALL>.json, and two automatic checks run on every entry before
it even reaches that file:
  * its "where" phrase must appear literally in the transcript text (the same principle as the "bad_gold" check
    evaluate_snapshot() already runs on hand-written gold's phrases -- catches a hallucinated fact for free)
  * its "metric" must be one this call's own vocabulary (core + its assigned packs, see vocab.py) actually defines
Anything that fails either check is kept in the file under "rejected_by_automatic_checks", visible, not silently
dropped. What survives is more likely to be real, but still needs a human or Claude to spot-check a sample against
the transcript (--verify-sample) before anything here is promoted into gold/ by hand -- never treat a draft as
ground truth on its own; a wrong entry would silently corrupt every future comparison against it.

One-time setup (free): create an API key at https://aistudio.google.com/apikey, then in your own terminal
`setx GEMINI_API_KEY "your-key"` -- a NEW terminal is needed for a running session to see it, same as
ALPHAVANTAGE_API_KEY. If the default model name below has been retired, pass --model with a current one from
https://aistudio.google.com/ 's free-tier list.

    py draft_gold.py CPRT_2026Q3                        draft gold/drafts/CPRT_2026Q3.json
    py draft_gold.py MET_2026Q2 --verify-sample 8        also print 8 random entries to check by hand
    py draft_gold.py MET_2026Q2 --check --open           draft, then open it as an annotate.py checked page
                                                          (shows overlap with what the CURRENT extractor already
                                                          produces -- a diagnostic, not a substitute for reading
                                                          the transcript)
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import date
from pathlib import Path

import vocab
from annotate import find_call, write_page
from evaluate import ROOT, load_transcript
from facts import _TRIGGER_DEFS, extract_facts
from fetchers import scrub_secrets
from transcript import ROLE_MANAGEMENT, ROLE_UNKNOWN

GOLD_DRAFTS = ROOT / "gold" / "drafts"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# "-latest" aliases keep pointing at whatever Google currently serves, so this does not go stale the way a pinned
# version does (gemini-2.0-flash 404'd within this project's lifetime). List what your key can use with:
#   py draft_gold.py --list-models
# Prefer the lite alias: the full "-latest" flash model's free tier can be as low as 20 requests/DAY (hit in
# practice) -- a lite model is a separate quota pool, usually more generous, and still an alias that self-updates.
DEFAULT_MODEL = "gemini-flash-lite-latest"

_KINDS = ["reported", "guidance"]
_STATS = ["level", "growth", "change"]
_UNITS = ["USD", "USD_per_share", "pct", "bps", "pp"]
_BASES = ["yoy", "qoq"]
_ACCOUNTING = ["gaap", "non_gaap"]

# A few real, hand-written entries (trimmed) to anchor the value/unit conventions in the prompt.
_FEW_SHOT = [
    {"kind": "reported", "metric": "revenue", "segment": "total", "stat": "level", "unit": "USD", "value": 11500000000,
     "change": {"value": 50, "unit": "pct", "basis": "yoy"}, "where": "Revenue increased 50% year-over-year to $11.5 billion"},
    {"kind": "reported", "metric": "net_income", "segment": "total", "stat": "level", "unit": "USD", "value": 174000000,
     "where": "net income was $174 million"},
    {"kind": "guidance", "metric": "ebitda", "segment": "total", "stat": "level", "unit": "USD", "value": 1225000000,
     "value_high": 1275000000, "period": "full year", "where": "adjusted EBITDA of between $1.225 billion and $1.275 billion"},
    {"kind": "reported", "metric": "eps", "segment": "total", "stat": "growth", "unit": "pct", "value": 2.4, "basis": "yoy",
     "where": "earnings per diluted share increased 2.4%"},
    {"kind": "reported", "metric": "gross_margin", "segment": "u.s.", "stat": "level", "unit": "pct", "value": 48.3,
     "where": "gross profit margin was 48.3%"},
]


class GeminiError(RuntimeError):
    """Gemini answered, but with an error, or not in the shape expected."""


class QuotaExhaustedError(GeminiError):
    """The free tier's PER-DAY quota for this specific model is used up. Retrying now cannot help -- the daily
    quota does not refill on a short timer the way a per-minute rate limit does -- so the caller should stop
    immediately rather than retry or try a schema-less fallback against the same model."""


def _quota_is_daily(resp) -> bool:
    """True if a 429 response is specifically a per-day quota violation (not a short-lived per-minute limit)."""
    try:
        body = resp.json()
    except ValueError:
        return False
    violations = (body.get("error", {}).get("details") or [{}])
    for d in violations:
        for v in d.get("violations", []):
            if "PerDay" in (v.get("quotaId") or ""):
                return True
    return "per day" in json.dumps(body).lower()


def _scrub(text: str, key: str | None) -> str:
    return scrub_secrets(text, key)


def management_text(transcript) -> str:
    """The same slice of the call the extractor itself reads: management speech only, both sections, one line per
    turn as 'Speaker: sentence sentence ...' so the model has speaker context without seeing anything it shouldn't."""
    lines = []
    for turn in transcript.turns:
        if turn.role not in (ROLE_MANAGEMENT, ROLE_UNKNOWN):
            continue
        text = " ".join(s.text for s in turn.sentences)
        if text.strip():
            lines.append(f"{turn.speaker}: {text}")
    return "\n\n".join(lines)


def available_metrics(ticker: str | None) -> dict[str, str]:
    """{metric name: trigger pattern} for this ticker: the core vocabulary plus its assigned packs -- exactly what
    the extractor itself would recognise, so a drafted fact can only use a name the extractor could actually match."""
    metrics = {name: pattern for name, pattern in _TRIGGER_DEFS}
    for pack_name in vocab.packs_for(ticker):
        try:
            pack = json.loads((vocab.VOCAB_DIR / f"{pack_name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for m in pack.get("metrics", []):
            metrics[m["name"]] = m["pattern"]
    return metrics


def _response_schema(metric_names: list[str]) -> dict:
    entry = {
        "type": "OBJECT",
        "properties": {
            "kind": {"type": "STRING", "enum": _KINDS},
            "metric": {"type": "STRING", "enum": metric_names},
            "segment": {"type": "STRING"},
            "stat": {"type": "STRING", "enum": _STATS},
            "unit": {"type": "STRING", "enum": _UNITS},
            "value": {"type": "NUMBER"},
            "value_high": {"type": "NUMBER"},
            "period": {"type": "STRING"},
            "basis": {"type": "STRING", "enum": _BASES},
            "accounting": {"type": "STRING", "enum": _ACCOUNTING},
            "where": {"type": "STRING"},
        },
        "required": ["kind", "metric", "segment", "stat", "unit", "value", "where"],
    }
    return {"type": "OBJECT",
            "properties": {"expected": {"type": "ARRAY", "items": entry},
                           "unmapped": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["expected", "unmapped"]}


def build_prompt(transcript, metrics: dict[str, str]) -> str:
    metric_lines = "\n".join(f'  {name}: matches phrasing like "{pattern}"' for name, pattern in sorted(metrics.items()))
    examples = "\n".join(json.dumps(e) for e in _FEW_SHOT)
    return f"""You are drafting a GOLD ANSWER KEY for a fact-extraction system that reads earnings-call transcripts. \
Read ONLY the transcript at the end of this prompt. Do not use any outside knowledge about this company. List every \
quantitative claim management makes about the business -- both reported results and forward guidance -- as one JSON \
object per claim in the "expected" array. Be thorough: it is much worse to miss a real claim than to list one extra.

Each object has these fields:
  kind      "reported" (a result already achieved) or "guidance" (a forward-looking figure, a target, a range)
  metric    ONE of the exact names listed below -- never invent a new one. If a real quantitative claim does not
            fit any of them, do NOT force it into a wrong metric: instead add a short plain-English note about it
            to the separate "unmapped" array (e.g. "customer count of 50 million, no matching metric").
  segment   "total" for a company-wide figure; otherwise the exact business unit named, lowercase (e.g. "u.s.",
            "data center", "gathering")
  stat      "level" (a point-in-time figure), "growth" (a percent change), or "change" (a dollar or bps change)
  unit      "USD" (the RAW dollar amount, e.g. 6700000000 for $6.7 billion -- never "6.7" or "6.7B"), "USD_per_share",
            "pct" (the raw number, e.g. 4.6 for "4.6%" -- never 0.046), "bps", or "pp"
  value     the number itself (see unit above for the exact scale expected)
  value_high  only for a stated range ("between $X and $Y"): the top of the range
  period    the period as the call itself names it (e.g. "second quarter", "full year"), or omit if unclear
  basis     "yoy" or "qoq", only if the change has one
  accounting  "gaap" or "non_gaap", only if the call says so explicitly
  where     a SHORT phrase (roughly 4-15 words) copied VERBATIM from the transcript that contains this claim -- not
            paraphrased, not summarized. This is checked automatically against the transcript text; if it is not an
            exact substring, the whole entry is discarded, so copy it exactly.

Available metric names for THIS call:
{metric_lines}

Five example entries showing the exact value/unit conventions (from other, unrelated calls):
{examples}

Rules:
  * Every fact must be a literal statement in the transcript below. Never compute, infer, or estimate a value that
    is not itself stated somewhere in the text.
  * A number that is a comparison point ("up from $X a year ago") or an industry/market statistic (not this specific
    company's own result) is not a fact to include.
  * If the same fact is stated more than once (e.g. by both the CEO and the CFO), include it only once.

Transcript (management speech only):
---
{management_text(transcript)}
---

Return the JSON object now."""


def list_models(api_key: str) -> list[str]:
    """Model names this key can use for generateContent, for when the default (or a pinned --model) 404s because
    Google retired it -- model names change faster than this file does."""
    import requests

    def scrub(text: str) -> str:
        return _scrub(text, api_key)

    try:
        resp = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": api_key}, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        raise GeminiError(f"Gemini model list failed ({type(exc).__name__}): {scrub(str(exc))}") from None
    models = resp.json().get("models", [])
    return sorted(m["name"].removeprefix("models/") for m in models if "generateContent" in m.get("supportedGenerationMethods", []))


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}   # rate-limited or temporarily overloaded, not a real failure
_JSON_INSTRUCTION = ("\n\nRespond with ONLY the JSON object described above. No markdown code fences, no "
                     "commentary before or after it.")


def _post_once(prompt: str, api_key: str, model: str, schema: dict | None, timeout: int,
               max_retries: int, base_delay: float, scrub) -> dict:
    """One request, retried on transient errors (rate limit / temporarily overloaded) only. Returns the parsed
    response body; raises GeminiError on a non-retryable failure or once the retries are exhausted."""
    import time
    import requests

    config = {"temperature": 0.1}
    if schema is not None:
        config.update(responseMimeType="application/json", responseSchema=schema)
    resp = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(GEMINI_URL.format(model=model), params={"key": api_key},
                                 json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
                                 timeout=timeout)
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise GeminiError(f"Gemini request failed after {attempt + 1} attempt(s) "
                                  f"({type(exc).__name__}): {scrub(str(exc))}") from None
            delay = base_delay * 2 ** attempt
            print(f"Gemini request failed ({type(exc).__name__}); retrying in {delay:.0f}s "
                  f"({attempt + 1}/{max_retries})...", file=sys.stderr)
            time.sleep(delay)
            continue
        if resp.status_code == 429 and _quota_is_daily(resp):
            raise QuotaExhaustedError(
                f"The free-tier daily quota for model {model!r} is used up. This will not recover with a retry "
                f"(it is a per-day limit, not a short rate limit) -- try again tomorrow, or pass --model with a "
                f"different model (a lite variant has a separate quota; see --list-models).")
        if resp.status_code in _RETRYABLE_STATUS and attempt < max_retries:
            delay = base_delay * 2 ** attempt
            print(f"Gemini returned {resp.status_code} (temporary, likely just overloaded); retrying in {delay:.0f}s "
                  f"({attempt + 1}/{max_retries})...", file=sys.stderr)
            time.sleep(delay)
            continue
        break
    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise GeminiError(f"Gemini request failed ({type(exc).__name__}): {scrub(str(exc))}") from None
    try:
        data = resp.json()
    except ValueError:
        raise GeminiError(f"Non-JSON response from Gemini: {scrub(resp.text[:300])!r}") from None
    if "error" in data:
        raise GeminiError(scrub(f"Gemini: {data['error'].get('message', data['error'])}"))
    return data


# Strips only an OUTERMOST markdown fence (```json ... ``` or ``` ... ```), anchored to the true start/end of the
# text (no re.MULTILINE) so nothing inside the JSON body -- which should not contain literal triple-backticks, but
# better safe -- is touched.
_FENCE = re.compile(r"\A```(?:json)?[ \t]*\n?|\n?```[ \t]*\Z", re.I)


def _extract_json(data: dict, scrub) -> dict:
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise GeminiError(f"Unexpected Gemini response shape: {scrub(json.dumps(data)[:300])}")
    stripped = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")            # last resort: the outermost { ... }
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except ValueError:
            pass
    raise GeminiError(f"Gemini did not return valid JSON: {scrub(text[:300])!r}")


def call_gemini(prompt: str, schema: dict, api_key: str, model: str, timeout: int = 120,
                max_retries: int = 4, base_delay: float = 3.0) -> dict:
    """Tries structured output (a hard guarantee the metric name stays in-vocabulary) first. If that specifically
    fails -- seen in practice: responseSchema alone returns a transient 503 while a plain request to the same model
    succeeds -- falls back to asking for JSON via the prompt text and parses defensively (_extract_json handles a
    markdown-fenced reply). The caller's own two checks (metric-in-vocabulary, where-found-verbatim) still guard the
    fallback path, so this is not a safety regression, only a smaller guarantee up front."""
    def scrub(text: str) -> str:
        return _scrub(text, api_key)

    try:
        data = _post_once(prompt, api_key, model, schema, timeout, max_retries, base_delay, scrub)
    except QuotaExhaustedError:
        raise  # the fallback would hit the identical per-model daily quota: no point spending it too
    except GeminiError as structured_exc:
        print(f"Structured-output request failed ({structured_exc}); retrying without response-schema "
              f"enforcement...", file=sys.stderr)
        data = _post_once(prompt + _JSON_INSTRUCTION, api_key, model, None, timeout, max_retries=1,
                          base_delay=base_delay, scrub=scrub)
    return _extract_json(data, scrub)


def filter_entries(raw_expected: list[dict], metrics: dict[str, str], text_lower: str) -> tuple[list[dict], list[dict]]:
    """(kept, rejected). Every kept entry passed both automatic checks; every rejected one carries why."""
    kept, rejected = [], []
    for e in raw_expected:
        where = (e.get("where") or "").strip()
        reason = None
        if e.get("metric") not in metrics:
            reason = f"metric {e.get('metric')!r} is not in this call's vocabulary"
        elif not where or where.lower() not in text_lower:
            reason = "'where' phrase not found verbatim in the transcript (likely paraphrased or hallucinated)"
        if reason:
            rejected.append({**e, "_rejected_reason": reason})
        else:
            kept.append(e)
    return kept, rejected


def draft(call_stem: str, api_key: str, model: str = DEFAULT_MODEL) -> dict:
    path = find_call(call_stem)
    transcript = load_transcript(path)
    metrics = available_metrics(transcript.meta.get("ticker"))
    if not metrics:
        raise GeminiError(f"No metric vocabulary resolved for {call_stem} (unknown ticker?) -- nothing to draft against.")
    prompt = build_prompt(transcript, metrics)
    schema = _response_schema(sorted(metrics))
    raw = call_gemini(prompt, schema, api_key, model)
    kept, rejected = filter_entries(raw.get("expected", []), metrics, transcript.text.lower())
    try:
        rel_transcript = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        rel_transcript = str(path)
    return {
        "transcript": rel_transcript,
        "notes": (f"UNVERIFIED DRAFT, machine-generated by draft_gold.py ({model}) from the transcript alone on "
                  f"{date.today().isoformat()}. Every entry's 'where' phrase was checked to literally appear in the "
                  f"transcript and its metric to be one the call's own vocabulary defines, but NEITHER check confirms "
                  f"the fact is actually correct. Do not treat this as trusted gold, and do not copy it into gold/ "
                  f"without spot-checking entries against the transcript first (see --verify-sample)."),
        "expected": kept,
        "forbidden": [],
        "rejected_by_automatic_checks": rejected,
        "unmapped_claims_gemini_flagged": raw.get("unmapped", []),
    }


def write_draft(call_stem: str, result: dict, out_dir: Path = GOLD_DRAFTS) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{call_stem}.json"
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def print_verify_sample(result: dict, n: int, seed: str) -> None:
    pool = result["expected"]
    if not pool:
        print("Nothing survived the automatic checks -- nothing to sample.")
        return
    sample = random.Random(seed).sample(pool, min(n, len(pool)))
    print(f"\n{len(sample)} of {len(pool)} kept entries, for you to check against the transcript by hand:")
    for e in sample:
        rng = f"{e['value']} to {e['value_high']}" if e.get("value_high") is not None else e["value"]
        print(f"  [{e['kind']}] {e['metric']} / {e.get('segment', '?')} / {e['stat']} = {rng} {e['unit']}"
              f"{' (' + e['period'] + ')' if e.get('period') else ''}")
        print(f"      where: \"{e['where']}\"")


def main() -> int:
    import os

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("call", nargs="?", help="cache name without extension, e.g. MET_2026Q2")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--list-models", action="store_true", help="print models your key can use, then exit")
    ap.add_argument("--verify-sample", type=int, default=5, metavar="N")
    ap.add_argument("--out", default=str(GOLD_DRAFTS))
    ap.add_argument("--check", action="store_true", help="also render an annotate.py checked page from the draft")
    ap.add_argument("--open", action="store_true", help="open the checked page (implies --check)")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("No GEMINI_API_KEY set. Create a free key at https://aistudio.google.com/apikey, then in your own "
              "terminal: setx GEMINI_API_KEY \"your-key\" -- a NEW terminal is needed for this one to see it.",
              file=sys.stderr)
        return 1

    if args.list_models:
        try:
            for name in list_models(api_key):
                print(name)
        except GeminiError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if not args.call:
        ap.error("call is required unless --list-models is given")

    try:
        result = draft(args.call, api_key, args.model)
    except GeminiError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    path = write_draft(args.call, result, Path(args.out))
    n_kept, n_rej = len(result["expected"]), len(result["rejected_by_automatic_checks"])
    print(f"{path}\n{n_kept} kept, {n_rej} rejected by automatic checks, "
          f"{len(result['unmapped_claims_gemini_flagged'])} unmapped claims flagged.")
    if n_rej:
        print("Rejected (shown for transparency, not written as gold):")
        for r in result["rejected_by_automatic_checks"][:10]:
            print(f"   {r.get('metric')}: {r['_rejected_reason']}")
    if args.verify_sample:
        print_verify_sample(result, args.verify_sample, seed=args.call)

    if args.check or args.open:
        transcript = load_transcript(find_call(args.call))
        current = extract_facts(transcript)
        page = write_page(transcript, current, Path("output") / "review", gold=result, label="gemini_draft")
        print(f"\nchecked page (draft vs. what the CURRENT extractor already produces): {page}")
        if args.open and hasattr(os, "startfile"):
            os.startfile(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
evaluate.py
Scores fact extraction against hand-verified gold files in gold/*.json.

Gold file format:
{
  "transcript": "data/transcripts/AAPL_Q3_2026.txt",      (.txt, or an Alpha Vantage .json)
  "expected":  [ {"kind": "reported", "metric": "revenue", "segment": "total", "stat": "level",
                  "unit": "USD", "value": 109.4e9, "change": {"value": 16, "unit": "pct", "basis": "yoy"}} ],
  "forbidden": [ {"metric": "revenue", "segment": "total", "value": 108e9} ]
}
An expected/forbidden entry only compares the keys it contains (plus `change` and
`descriptor_contains`). Numbers match to 1e-9 relative tolerance.

  py evaluate.py                      run every gold file, exit code 1 if the gate fails
  py evaluate.py --min-recall 0.9     loosen the recall requirement (default 1.0)
  py evaluate.py --show-extras        list high-confidence facts that are not in the gold file (to review/add)

The gate a call must pass before its facts are loaded anywhere:
  * every fact verifies against the transcript text
  * no forbidden fact is produced
  * recall on the gold set >= --min-recall
"""

import argparse
import json
import sys
from pathlib import Path

from facts import extract_facts
from fetchers import _read_text
from transcript import build_from_structured, parse_transcript

ROOT = Path(__file__).resolve().parent
_KEYS = ("kind", "metric", "segment", "stat", "unit", "period", "driver", "basis", "accounting",
         "currency_basis", "direction")


def pending_gold() -> dict:
    """{call stem: reason} for gold files still being worked on (gold/PENDING.json); the tests skip them."""
    path = ROOT / "gold" / "PENDING.json"
    return json.loads(path.read_text(encoding="utf-8")).get("pending", {}) if path.exists() else {}


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def matches(fact: dict, want: dict) -> bool:
    for k in _KEYS:
        if k in want and fact.get(k) != want[k]:
            return False
    for k in ("value", "value_high"):
        if k in want and not _close(fact.get(k), want[k]):
            return False
    if "descriptor_contains" in want and want["descriptor_contains"].lower() not in (fact.get("descriptor") or "").lower():
        return False
    if "change" in want:
        wc = want["change"]
        if not any(_close(c["value"], wc["value"]) and c["unit"] == wc.get("unit", c["unit"])
                   and c.get("basis") == wc.get("basis", c.get("basis")) for c in fact.get("changes", [])):
            return False
    return True


def load_transcript(path: Path):
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return build_from_structured(payload["transcript"], path.name, {"quarter_label": payload.get("quarter")})
    return parse_transcript(_read_text(path), path.name)


def evaluate(gold_path: Path, show_extras: bool = False) -> dict:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    result = extract_facts(load_transcript(ROOT / gold["transcript"]))
    facts = result["facts"]

    missed = [w for w in gold.get("expected", []) if not any(matches(f, w) for f in facts)]
    forbidden_hits = [(w, f) for w in gold.get("forbidden", []) for f in facts if matches(f, w)]
    expected = gold.get("expected", [])
    matched_ids = {id(f) for w in expected for f in facts if matches(f, w)}
    extras = [f for f in facts if id(f) not in matched_ids and f["confidence"] == "high"]
    return {
        "gold": gold_path.name, "expected": len(expected), "missed": missed,
        "recall": (len(expected) - len(missed)) / len(expected) if expected else 1.0,
        "forbidden_hits": forbidden_hits, "unverified": result["stats"]["unverified"],
        "checks_failed": result["stats"]["checks_failed"], "extras": extras if show_extras else [],
        "n_extras": len(extras), "n_facts": len(facts),
    }


def signal_matches(sig: dict, want: dict) -> bool:
    """Gold signal entry vs an extracted signal: `contains` is a case-insensitive substring of the sentence; topic and
    segment match any of the signal's lists; direction/horizon/stance/revision must match exactly."""
    if want["contains"].lower() not in sig["sentence"].lower():
        return False
    if "topic" in want and want["topic"] not in sig["topics"]:
        return False
    if "segment" in want and want["segment"] not in sig["segments"]:
        return False
    return all(sig.get(k) == want[k] for k in ("direction", "horizon", "stance", "revision") if k in want)


def evaluate_snapshot(gold_path: Path) -> dict:
    """
    Gate for the business-snapshot layer against gold/snapshot/<call>.json:
      * gold sanity: every phrase in the gold file exists in the transcript (catches gold typos)
      * every signal verifies (real sentence, right place, management speaker)
      * expected signals are found with the right topic/direction/horizon/stance/segment
      * no forbidden statement (analyst line, safe-harbor boilerplate, pleasantry) becomes a signal
      * expected segments have ledger facts / commentary in the breakdown
      * expected reported-vs-guided verdicts are produced
      * must-surface statements actually make it onto the page
    """
    from snapshot import build_snapshot

    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    transcript = load_transcript(ROOT / gold["transcript"])
    facts_result = extract_facts(transcript)
    snap = build_snapshot(transcript, facts_result)
    sigs = snap["signals"]
    text = transcript.text.lower()

    # A must-surface entry is a *message*: a phrase, or a list of acceptable phrasings (management repeats key points in
    # different words, and the page shows each message once).
    must = [[m] if isinstance(m, str) else list(m) for m in gold.get("must_surface", [])]
    phrases = ([w["contains"] for w in gold.get("signals_expected", []) + gold.get("signals_forbidden", [])]
               + [alt for m in must for alt in m])
    bad_gold = [p for p in phrases if p.lower() not in text]

    missed = [w for w in gold.get("signals_expected", []) if not any(signal_matches(s, w) for s in sigs)]
    forbidden = [(w, s) for w in gold.get("signals_forbidden", []) for s in sigs if signal_matches(s, w)]
    blocks = {b["id"]: b for b in snap["blocks"]}
    no_facts = [n for n in gold.get("segments_with_facts", []) if not blocks.get(n, {}).get("fact_ids")]
    no_comment = [n for n in gold.get("segments_with_commentary", []) if not blocks.get(n, {}).get("n_mentions")]
    traj = {(r["node"], r["metric"]): r for r in snap["trajectory"]}
    wrong_traj = [w for w in gold.get("trajectory_expected", [])
                  if traj.get((w["node"], w["metric"]), {}).get("direction") != w["direction"]]
    surfaced = [s["sentence"].lower() for s in sigs if s["id"] in set(snap["surfaced_signal_ids"])]
    not_surfaced = [m[0] for m in must if not any(alt.lower() in s for alt in m for s in surfaced)]
    expected = gold.get("signals_expected", [])
    return {
        "gold": gold_path.name, "expected": len(expected), "missed": missed,
        "recall": (len(expected) - len(missed)) / len(expected) if expected else 1.0,
        "forbidden_hits": forbidden, "bad_gold": bad_gold, "no_facts": no_facts, "no_comment": no_comment,
        "wrong_traj": wrong_traj, "not_surfaced": not_surfaced,
        "unverified": sum(1 for s in sigs if not s["verified"]), "checks_failed": snap["stats"]["checks_failed"],
        "n_signals": len(sigs), "n_surfaced": len(surfaced),
    }


def facts_ok(r: dict, min_recall: float = 1.0) -> bool:
    """The rule a call's facts must satisfy against its gold file (also used by batch.py)."""
    return (r["recall"] >= min_recall and not r["forbidden_hits"] and r["unverified"] == 0 and r["checks_failed"] == 0)


def snapshot_ok(r: dict, min_recall: float = 1.0) -> bool:
    """The rule a call's snapshot must satisfy against its snapshot gold file."""
    return (r["recall"] >= min_recall and not r["forbidden_hits"] and not r["bad_gold"] and not r["no_facts"]
            and not r["no_comment"] and not r["wrong_traj"] and not r["not_surfaced"] and r["unverified"] == 0
            and r["checks_failed"] == 0)


def _report_snapshot(r: dict, min_recall: float) -> bool:
    ok = snapshot_ok(r, min_recall)
    print(f"{'PASS' if ok else 'FAIL'} snapshot/{r['gold']}: signal recall {r['recall']:.0%} "
          f"({r['expected'] - len(r['missed'])}/{r['expected']}), forbidden {len(r['forbidden_hits'])}, "
          f"segments missing facts {len(r['no_facts'])} / commentary {len(r['no_comment'])}, "
          f"trajectory wrong {len(r['wrong_traj'])}, must-surface missing {len(r['not_surfaced'])}, "
          f"unverified {r['unverified']}, failed checks {r['checks_failed']} "
          f"({r['n_signals']} signals, {r['n_surfaced']} surfaced)")
    for p in r["bad_gold"]:
        print(f"   GOLD TYPO (phrase not in transcript) {p!r}")
    for w in r["missed"]:
        print(f"   MISSED   {json.dumps(w)}")
    for w, s in r["forbidden_hits"]:
        print(f"   FORBIDDEN {w['contains']!r} <- {s['sentence'][:100]}")
    for n in r["no_facts"]:
        print(f"   NO FACTS for segment {n}")
    for n in r["no_comment"]:
        print(f"   NO COMMENTARY for segment {n}")
    for w in r["wrong_traj"]:
        print(f"   TRAJECTORY {json.dumps(w)}")
    for p in r["not_surfaced"]:
        print(f"   NOT SURFACED {p!r}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("gold", nargs="*")
    ap.add_argument("--min-recall", type=float, default=1.0)
    ap.add_argument("--show-extras", action="store_true")
    ap.add_argument("--facts-only", action="store_true", help="skip the business-snapshot gate")
    args = ap.parse_args()

    paths = [Path(p) for p in args.gold] or sorted((ROOT / "gold").glob("*.json"))
    if not paths:
        print("No gold files found in gold/.")
        return 1
    failed = False
    if not args.gold and not args.facts_only:
        for p in sorted((ROOT / "gold" / "snapshot").glob("*.json")):
            failed |= not _report_snapshot(evaluate_snapshot(p), args.min_recall)
    for p in paths:
        r = evaluate(p, args.show_extras)
        ok = facts_ok(r, args.min_recall)
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'} {r['gold']}: recall {r['recall']:.0%} "
              f"({r['expected'] - len(r['missed'])}/{r['expected']}), forbidden hits {len(r['forbidden_hits'])}, "
              f"unverified {r['unverified']}, failed checks {r['checks_failed']}, "
              f"{r['n_extras']} high-confidence facts not in gold (review these)")
        for w in r["missed"]:
            print(f"   MISSED   {json.dumps(w)}")
        for w, f in r["forbidden_hits"]:
            print(f"   FORBIDDEN {json.dumps(w)} <- {f['metric']}/{f['segment']} {f['figure_text']!r}: {f['sentence'][:100]}")
        for f in r["extras"]:
            print(f"   extra    {f['kind']} {f['metric']}/{f['segment']}/{f['stat']} {f['figure_text']!r} [{f['period']}]")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

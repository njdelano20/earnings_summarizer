"""
gold_summary.py
One compact table of how every call with an answer key scores, so a change to the extractor can be judged on all of
them at once.

    py gold_summary.py            facts and snapshot columns for every gold file
    py gold_summary.py --detail   also list what is missed / forbidden / not surfaced, per call

Columns:  facts = expected facts found / expected, F = forbidden facts produced;
          sig = expected signals found / expected, F = forbidden signals produced, S = must-surface messages missing.
A call with no misses and no forbidden hits is shown with a tick.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evaluate import ROOT, evaluate, evaluate_snapshot, facts_ok, pending_gold, snapshot_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args()
    stems = sorted(p.stem for p in (ROOT / "gold").glob("*.json") if p.name != "PENDING.json")
    pending = pending_gold()
    rows, details = [], []
    for stem in stems:
        f = evaluate(ROOT / "gold" / f"{stem}.json")
        snap_path = ROOT / "gold" / "snapshot" / f"{stem}.json"
        s = evaluate_snapshot(snap_path) if snap_path.exists() else None
        ok = facts_ok(f) and (s is None or snapshot_ok(s))
        rows.append((stem, f, s, ok))
        if args.detail:
            lines = [f"--- {stem}"]
            for w in f["missed"]:
                lines.append(f"   MISSED fact   {w.get('kind','')} {w.get('metric')} / {w.get('segment', '-')} = "
                             f"{w.get('value', w.get('descriptor_contains'))}")
            for w, fact in f["forbidden_hits"]:
                lines.append(f"   FORBIDDEN fact {fact['kind']} {fact['metric']} / {fact['segment']} = {fact['figure_text']}: "
                             f"{fact['sentence'][:80]}")
            if s:
                for w in s["missed"]:
                    lines.append(f"   MISSED signal {w['contains'][:100]}")
                for w, sig in s["forbidden_hits"]:
                    lines.append(f"   FORBIDDEN signal {w['contains'][:100]}")
                for p in s["not_surfaced"]:
                    lines.append(f"   NOT SURFACED  {p[:100]}")
                for n in s["no_facts"]:
                    lines.append(f"   NO FACTS for node {n}")
                for n in s["no_comment"]:
                    lines.append(f"   NO COMMENTARY for node {n}")
            details += lines
    print(f"{'call':<14} {'facts':>11} {'F':>3}   {'sig':>11} {'F':>3} {'S':>3}")
    for stem, f, s, ok in rows:
        fe = f["expected"]
        sig = f"{s['expected'] - len(s['missed'])}/{s['expected']}" if s else "-"
        print(f"{stem:<14} {fe - len(f['missed']):>4}/{fe:<6} {len(f['forbidden_hits']):>3}   {sig:>11} "
              f"{len(s['forbidden_hits']) if s else '-':>3} {len(s['not_surfaced']) if s else '-':>3}  "
              f"{'OK' if ok else ('(pending)' if stem in pending else 'REGRESSION')}")
    if args.detail:
        print("\n" + "\n".join(details))
    return 0


if __name__ == "__main__":
    sys.exit(main())

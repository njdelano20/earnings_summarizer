"""
snapshot.py
Assembles the business snapshot for one call: where each part of the business is, where management says it
is heading, and how management sounds about it. Built only from things that are already evidence-linked:

    ledger facts   (facts.py)     the numbers, with source sentence + confidence
    signals        (signals.py)   the qualitative statements, with source sentence + cue phrases
    registry       (segments.py)  the company's business map (segments -> sub-segments, geographies, ...)

Nothing is written freehand: every line of the snapshot is a fact row, a verbatim management sentence, or a
mechanical comparison of the two (the trajectory table). Selection (which sentences make the cut) is a scored,
deduplicated, capped pick, and the full unselected list stays in the JSON.

build_snapshot() output: {"meta", "headline", "company", "blocks", "trajectory", "tone", "signals",
                          "selected_signal_ids", "coverage", "checks", "stats"}
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from segments import DIMENSIONS, Registry, TOTAL, load_registry
from signals import extract_signals
from transcript import Transcript
from writer import fmt_changes, fmt_number, fmt_value, range_text

_BUCKETS = ["momentum", "product", "competition", "pressure", "strategy", "outlook"]
# Caps are generous for the topics a reader wants most (product, competition, outlook) because each is scarce.
_BUCKET_CAP_TOP = {"momentum": 3, "product": 3, "competition": 3, "pressure": 3, "strategy": 2, "outlook": 3}
_BUCKET_CAP_SUB = {"momentum": 2, "product": 1, "competition": 2, "pressure": 2, "strategy": 1, "outlook": 2}
_NODE_CAP_TOP, _NODE_CAP_SUB = 14, 6
_COMPANY_CAP = {"momentum": 4, "product": 2, "competition": 2, "pressure": 2, "strategy": 2, "outlook": 5, "leadership": 1}

_METRIC_ORDER = ["revenue", "gross_margin", "gross_profit", "operating_income", "operating_margin", "operating_expenses",
                 "ebitda", "net_income", "eps", "free_cash_flow", "operating_cash_flow", "signings", "backlog", "arr",
                 "cash", "cash_and_securities", "total_debt", "capital_returned", "dividends", "share_repurchases",
                 "oie", "tax_rate"]
_KIND_ORDER = {"reported": 0, "guidance": 1, "declared": 2}
_LEVEL_TRAJECTORY_METRICS = {"gross_margin", "operating_margin"}
_FLAT_TOLERANCE = 0.05


# =========================================================================== #
# Helpers
# =========================================================================== #

def _bucket(sig: dict) -> str | None:
    if sig["horizon"] == "forward":
        return "outlook"
    for t in sig["topics"]:
        if t in _BUCKETS:
            return t
    return None


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$%.]+", text.lower()) if len(w) > 3}


def _numbers(text: str) -> set[str]:
    return {n for n in re.findall(r"\$?\d[\d.,]*%?", text) if len(n.strip("$%.,")) >= 2 or "." in n}


def _sim(a: str, b: str) -> float:
    """0..1 'is this the same message?': word overlap (containment for longer sentences), 1.0 if two-plus figures match
    (the CEO and CFO both quoting the headline number)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    if len(_numbers(a) & _numbers(b)) >= 2:
        return 1.0
    jaccard = len(ta & tb) / len(ta | tb)
    contain = len(ta & tb) / min(len(ta), len(tb)) if min(len(ta), len(tb)) >= 5 else 0.0
    return max(jaccard, contain)


_DUPLICATE_AT = 0.7
_REDUNDANCY_PENALTY = 2.0


def _similar(a: str, b: str) -> bool:
    return _sim(a, b) >= _DUPLICATE_AT


def _usable(sig: dict) -> bool:
    return sig["verified"] and sig["confidence"] != "low"


def _fact_usable(f: dict) -> bool:
    return f["verified"] and f["confidence"] != "low"


def _fact_sort_key(f: dict):
    m = f["metric"]
    return (_KIND_ORDER.get(f["kind"], 9), _METRIC_ORDER.index(m) if m in _METRIC_ORDER else 99, f["stat"], f["period"] or "")


def _select(cands: list[dict], caps: dict[str, int], total_cap: int, taken: list[dict],
            bucket_of=_bucket) -> list[dict]:
    """
    Maximal-marginal-relevance pick: at each step take the candidate with the best score *minus a penalty for repeating
    what is already on the page*, within per-bucket caps. Near-duplicates of anything taken anywhere are excluded, so a
    message management repeats three ways takes one slot and leaves room for a different point.
    """
    pool = [s for s in cands if bucket_of(s) in caps and caps[bucket_of(s)] > 0]
    per_bucket: Counter = Counter()
    picked: list[dict] = []
    while pool and len(picked) < total_cap:
        best = None
        for s in pool:
            b = bucket_of(s)
            if per_bucket[b] >= caps[b]:
                continue
            sim = max((_sim(s["sentence"], t["sentence"]) for t in taken + picked), default=0.0)
            if sim >= _DUPLICATE_AT:
                continue
            key = (s["score"] - _REDUNDANCY_PENALTY * sim, -s["turn_index"], -s["sentence_index"])
            if best is None or key > best[0]:
                best = (key, s)
        if best is None:
            break
        picked.append(best[1])
        per_bucket[bucket_of(best[1])] += 1
        pool.remove(best[1])
    return sorted(picked, key=lambda s: (s["turn_index"], s["sentence_index"]))


# =========================================================================== #
# Trajectory: reported -> guided, compared mechanically
# =========================================================================== #

def _band_from_descriptor(desc: str | None) -> tuple[float, float] | None:
    if not desc:
        return None
    from signals import _BANDS
    m = re.match(r"\s*(low|mid|high|upper|lower)[- ]?(single|double|teens|twenties)", desc.lower())
    return _BANDS.get((m.group(1), m.group(2))) if m else None


def _reported_growth(facts: list[dict], node: str, registry: Registry, call_label: str | None) -> dict | None:
    best = None
    for f in facts:
        if f["kind"] != "reported" or f["metric"] != "revenue" or not _fact_usable(f):
            continue
        if registry.node_for_fact_segment(f["segment"]) != node:
            continue
        value = None
        if f["stat"] == "growth" and f["unit"] == "pct" and f["value"] is not None and f["basis"] in ("yoy", None):
            value = f["value"]
        elif f["stat"] == "level":
            for c in f.get("changes", []):
                if c["unit"] == "pct" and c.get("basis") in ("yoy", None):
                    value = c["value"]
                    break
        if value is None:
            continue
        rank = ((f["period"] == call_label), f["section"] == "prepared", f["confidence"] == "high", f["currency_basis"] is None)
        if best is None or rank > best[0]:
            best = (rank, {"value": value, "period": f["period"], "fact_id": f["id"], "basis": "yoy",
                           "currency_basis": f["currency_basis"], "confidence": f["confidence"]})
    return best[1] if best else None


def _reported_level(facts: list[dict], node: str, metric: str, registry: Registry, call_label: str | None) -> dict | None:
    best = None
    for f in facts:
        if (f["kind"] != "reported" or f["metric"] != metric or f["stat"] != "level" or f["unit"] != "pct"
                or not _fact_usable(f) or f["value"] is None):
            continue
        if registry.node_for_fact_segment(f["segment"]) != node:
            continue
        rank = ((f["period"] == call_label), f["section"] == "prepared", f["confidence"] == "high")
        if best is None or rank > best[0]:
            best = (rank, {"value": f["value"], "period": f["period"], "fact_id": f["id"], "confidence": f["confidence"]})
    return best[1] if best else None


def _guided_growth(facts, signals, node, registry) -> dict | None:
    """Best available forward statement about revenue growth for `node`: ledger number > ledger descriptor > text band > text direction."""
    cands = []
    for f in facts:
        if (f["kind"] != "guidance" or f["metric"] != "revenue" or f["stat"] != "growth" or not _fact_usable(f)
                or registry.node_for_fact_segment(f["segment"]) != node):
            continue
        if f["value"] is not None:
            lo, hi = f["value"], f["value_high"] if f["value_high"] is not None else f["value"]
            q = f["qualifier"] or ""
            if q in _FLOOR_QUALIFIERS:            # "greater than 35%" is a floor, not a point
                hi = None
            elif q in _CEILING_QUALIFIERS:
                lo, hi = None, lo
            cands.append((4, {"lo": lo, "hi": hi, "descriptor": None, "period": f["period"], "source": "ledger",
                              "id": f["id"], "direction_word": None, "text": f["figure_text"], "qualifier": q or None,
                              "confidence": f["confidence"]}))
        else:
            band = _band_from_descriptor(f["descriptor"])
            word = None
            if band is None:
                if re.search(r"similar|flat|consistent", f["descriptor"] or "", re.I):
                    word = "flat"
                elif re.search(r"\bdecline|\bdecrease", f["descriptor"] or "", re.I):
                    word = "down"                # "significant double-digit decline": management states the direction
            if band or word:
                cands.append((3, {"lo": band[0] if band else None, "hi": band[1] if band else None,
                                  "descriptor": f["descriptor"], "period": f["period"], "source": "ledger", "id": f["id"],
                                  "direction_word": word, "text": f["descriptor"], "confidence": f["confidence"]}))
    for s in signals:
        g = s.get("guided")
        if not g or g["node"] != node or not _usable(s):
            continue
        if g["band"]:
            cands.append((2, {"lo": g["band"][0], "hi": g["band"][1], "descriptor": g["descriptor"], "period": None,
                              "source": "signal_text", "id": s["id"], "direction_word": g["direction_word"],
                              "text": s["sentence"], "position": g["position"], "modifier": g["modifier"],
                              "confidence": "medium"}))
        elif g["direction_word"]:
            cands.append((1, {"lo": None, "hi": None, "descriptor": None, "period": None, "source": "signal_text",
                              "id": s["id"], "direction_word": g["direction_word"], "text": s["sentence"],
                              "confidence": "medium"}))
    if not cands:
        return None
    # Source quality first; then the next-period guide (a quarter) before a long-horizon target ("2027"); then high confidence.
    cands.sort(key=lambda c: (-c[0], not re.search(r"quarter|^q[1-4]", c[1].get("period") or "", re.I),
                              c[1]["confidence"] != "high"))
    return cands[0][1]


_FLOOR_QUALIFIERS = {"greater than", "more than", "over", "above", "at least", "exceeds", "exceed", "higher than",
                     "in excess of"}
_CEILING_QUALIFIERS = {"less than", "under", "below", "at most", "up to"}


def _verdict(reported: float | None, lo, hi, word) -> tuple[str | None, float | None]:
    """Reported figure vs a guided range. lo or hi may be None for a floor ("greater than 35%") or a ceiling."""
    if reported is not None and (lo is not None or hi is not None):
        eps = 1e-9
        delta = ((lo + hi) / 2 if lo is not None and hi is not None else (lo if lo is not None else hi)) - reported
        if lo is not None and reported < lo - eps:
            return "up", delta
        if hi is not None and reported > hi + eps:
            return "down", delta
        return "flat", delta
    if word:
        return word, None
    return None, None


def build_trajectory(facts: list[dict], signals: list[dict], registry: Registry, call_label: str | None) -> list[dict]:
    rows = []
    nodes = [TOTAL] + [n.id for n in registry.nodes if n.dimension == "segment"]
    for node in nodes:
        name = "Total company" if node == TOTAL else registry.get(node).name
        rep = _reported_growth(facts, node, registry, call_label)
        gd = _guided_growth(facts, signals, node, registry)
        if gd is None:
            continue
        direction, delta = _verdict(rep["value"] if rep else None, gd["lo"], gd["hi"], gd["direction_word"])
        if direction is None:
            continue
        conf = "high" if (gd["source"] == "ledger" and gd["confidence"] == "high" and (rep is None or rep["confidence"] == "high")) else "medium"
        note = []
        if gd["source"] == "signal_text":
            note.append("guided figure read from the sentence, not yet in the ledger")
        if rep is None:
            note.append("no reported growth figure for this segment")
        if rep and rep.get("currency_basis") == "constant":
            note.append("reported growth is constant-currency")
        rows.append({"key": f"{node}:revenue", "node": node, "name": name, "metric": "revenue", "kind": "growth",
                     "reported": rep, "guided": gd, "direction": direction,
                     "delta_pp": round(delta, 2) if delta is not None else None, "confidence": conf, "note": "; ".join(note)})
    for metric in sorted(_LEVEL_TRAJECTORY_METRICS):
        for node in [TOTAL] + [n.id for n in registry.nodes if n.dimension == "segment"]:
            rep = _reported_level(facts, node, metric, registry, call_label)
            if rep is None:
                continue
            g = [f for f in facts if f["kind"] == "guidance" and f["metric"] == metric and f["stat"] == "level"
                 and f["unit"] == "pct" and f["value"] is not None and _fact_usable(f)
                 and registry.node_for_fact_segment(f["segment"]) == node]
            if not g:
                continue
            g = g[0]
            hi = g["value_high"] if g["value_high"] is not None else g["value"]
            direction, delta = _verdict(rep["value"], g["value"], hi, None)
            name = ("Total company" if node == TOTAL else registry.get(node).name)
            rows.append({"key": f"{node}:{metric}", "node": node, "name": name, "metric": metric, "kind": "level",
                         "reported": rep, "guided": {"lo": g["value"], "hi": hi, "descriptor": None, "period": g["period"],
                                                     "source": "ledger", "id": g["id"], "direction_word": None,
                                                     "text": g["figure_text"], "confidence": g["confidence"]},
                         "direction": direction, "delta_pp": round(delta, 2), "confidence": g["confidence"], "note": ""})
    return rows


# =========================================================================== #
# Management stance
# =========================================================================== #

def build_tone(signals: list[dict]) -> dict:
    use = [s for s in signals if _usable(s)]
    by_section: dict[str, Counter] = defaultdict(Counter)
    by_speaker: dict[str, Counter] = defaultdict(Counter)
    for s in use:
        key = s["stance"] or "none"
        by_section[s["section"]][key] += 1
        if s["stance"]:
            by_speaker[s["speaker"]][s["stance"]] += 1
        if s["withheld"]:
            by_section[s["section"]]["withheld"] += 1

    def index(c: Counter) -> float | None:
        conf, caut = c["confident"], c["cautious"]
        return round((conf - caut) / (conf + caut), 2) if conf + caut else None

    sections = {sec: {"confident": c["confident"], "cautious": c["cautious"], "mixed": c["mixed"],
                      "withheld": c["withheld"], "index": index(c)} for sec, c in by_section.items()}
    conf_sigs = sorted([s for s in use if s["stance"] == "confident"],
                       key=lambda s: (-len(s["cues"].get("confident", [])), -(s["horizon"] == "forward"), -s["score"]))
    caut_sigs = sorted([s for s in use if s["stance"] in ("cautious", "mixed") and s["stance"] != "confident"],
                       key=lambda s: (-len(s["cues"].get("hedge", [])) - 2 * s["withheld"], -s["score"]))
    caut_sigs = [s for s in caut_sigs if s["stance"] == "cautious"]
    return {
        "method": "lexical cue counts on management sentences; indicative of wording, not a measure of business health",
        "sections": sections,
        "speakers": {k: dict(v) for k, v in by_speaker.items()},
        "notable_confident": [s["id"] for s in _diverse(conf_sigs, 4)],
        "notable_cautious": [s["id"] for s in _diverse(caut_sigs, 5)],
        "withheld": [s["id"] for s in use if s["withheld"]],
        "revisions": [s["id"] for s in use if s["revision"]],
    }


def _diverse(ranked: list[dict], n: int) -> list[dict]:
    out: list[dict] = []
    for s in ranked:
        if any(_similar(s["sentence"], t["sentence"]) for t in out):
            continue
        out.append(s)
        if len(out) >= n:
            break
    return out


# =========================================================================== #
# Assembly
# =========================================================================== #

def _total_revenue_conflicts(facts: list[dict]) -> list[str]:
    """Total-company revenue facts the ledger itself flagged as conflicting with another figure (any confidence).
    On a call where segment paragraphs say a bare 'Revenue was...', this is the tell that a segment number may have
    been filed under the company total."""
    return [f["id"] for f in facts if f["metric"] == "revenue" and f["segment"] == TOTAL
            and "conflict_with_other_value" in f["flags"]]


def _headline(facts: list[dict], trajectory: list[dict], tone: dict, call_label: str | None) -> list[dict]:
    lines = []

    def total(metric, kind="reported", stat="level"):
        # The headline quotes only high-confidence facts: a medium fact is fine in a table with its pill, not up here.
        c = [f for f in facts if f["kind"] == kind and f["metric"] == metric and f["segment"] == TOTAL
             and f["stat"] == stat and _fact_usable(f) and f["confidence"] == "high"]
        c.sort(key=lambda f: (f["period"] != call_label, f["section"] != "prepared", f["confidence"] != "high"))
        return c[0] if c else None

    rev = total("revenue")
    rev_g = total("revenue", stat="growth")
    if rev or rev_g:
        f = rev or rev_g
        txt = f"Revenue {fmt_value(f)}" if rev else f"Revenue growth {fmt_value(f)}"
        chg = fmt_changes(f)
        ids = [f["id"]]
        if not chg and rev and rev_g:          # level and growth were stated in separate sentences
            chg = f"{rev_g['value']:+g}%" + (" constant currency" if rev_g["currency_basis"] == "constant" else " yoy")
            ids.append(rev_g["id"])
        if chg:
            txt += f" ({chg})"
        if f["period"]:
            txt += f", {f['period']}"
        lines.append({"kind": "result", "text": txt, "fact_ids": ids})
    for metric, label in (("eps", "EPS"), ("gross_margin", "Gross margin"), ("operating_margin", "Operating margin"),
                          ("free_cash_flow", "Free cash flow"), ("operating_cash_flow", "Operating cash flow")):
        f = total(metric)
        if f:
            txt = f"{label} {fmt_value(f)}"
            if fmt_changes(f):
                txt += f" ({fmt_changes(f)})"
            lines.append({"kind": "result", "text": txt, "fact_ids": [f["id"]]})
    conflicts = _total_revenue_conflicts(facts)
    if conflicts and lines and lines[0]["text"].startswith("Revenue"):
        lines[0]["text"] += " (WARNING: the ledger holds conflicting total-company revenue figures, see Coverage)"
        lines[0]["fact_ids"] = list(dict.fromkeys(lines[0]["fact_ids"] + conflicts))
    verb = {"up": "accelerating", "down": "decelerating", "flat": "steady"}
    for row in trajectory:
        if row["node"] == TOTAL and row["metric"] == "revenue" and row["direction"] in verb:
            g, r = row["guided"], row["reported"]
            guide = (range_text(g["lo"], g["hi"]) if (g["lo"] is not None or g["hi"] is not None)
                     else (g["descriptor"] or g["direction_word"]))
            txt = f"Revenue growth is {verb[row['direction']]}: guided {guide}" + (
                f" vs {fmt_number(r['value'], 'pct')} reported" if r else "")
            lines.append({"kind": "trajectory", "text": txt, "fact_ids": [x for x in (g["id"] if g["source"] == "ledger" else None,
                                                                                    r["fact_id"] if r else None) if x]})
    secs = tone["sections"]
    parts = [f"{sec} {v['index']:+.2f}" for sec, v in secs.items() if v["index"] is not None]
    if parts:
        lines.append({"kind": "tone", "text": "Management wording (confident vs cautious cues, -1..+1): " + ", ".join(parts),
                      "fact_ids": []})
    return lines


def build_scoreboard(facts, registry, blocks, trajectory, call_label) -> list[dict]:
    """One row per business segment (plus the total): revenue, growth, margin, outlook read, management wording."""
    traj = {r["key"]: r for r in trajectory}
    by_id = {b["id"]: b for b in blocks}

    def revenue_level(node):
        best = None
        for f in facts:
            if (f["kind"] == "reported" and f["metric"] == "revenue" and f["stat"] == "level" and f["unit"] == "USD"
                    and _fact_usable(f) and registry.node_for_fact_segment(f["segment"]) == node):
                rank = (f["period"] == call_label, f["section"] == "prepared", f["confidence"] == "high")
                if best is None or rank > best[0]:
                    best = (rank, f)
        return best[1] if best else None

    rows = []
    entries = [(TOTAL, "Total company", 0)] + [(n.id, n.name, registry.depth(n.id)) for n in registry.nodes
                                                if n.dimension == "segment"]
    for node, name, depth in entries:
        lvl, growth = revenue_level(node), _reported_growth(facts, node, registry, call_label)
        margin = _reported_level(facts, node, "gross_margin", registry, call_label)
        tr = traj.get(f"{node}:revenue")
        block = by_id.get(node)
        if not (lvl or growth or margin or tr or (block and block["n_mentions"])):
            continue
        rows.append({
            "node": node, "name": name, "depth": depth,
            "revenue": {"value": lvl["value"], "fact_id": lvl["id"]} if lvl else None,
            "growth": growth, "gross_margin": margin,
            "outlook": ({"direction": tr["direction"], "source": tr["guided"]["id"], "delta_pp": tr["delta_pp"],
                         "guided_lo": tr["guided"]["lo"], "guided_hi": tr["guided"]["hi"],
                         "guided_descriptor": tr["guided"].get("descriptor")} if tr else None),
            "stance": block["stance"] if block else {}, "n_mentions": block["n_mentions"] if block else 0,
        })
    return rows


def build_snapshot(transcript: Transcript, facts_result: dict, registry: Registry | None = None) -> dict:
    facts = facts_result["facts"]
    meta = dict(facts_result["meta"])
    call_label = meta.get("reported_period_label")
    registry = registry or load_registry(meta.get("ticker"), [f["segment"] for f in facts])
    signals = extract_signals(transcript, registry, facts)
    sig_by_id = {s["id"]: s for s in signals}
    usable = [s for s in signals if _usable(s)]

    # ---- facts per node -------------------------------------------------------------------------
    facts_by_node: dict[str, list[dict]] = defaultdict(list)
    unmapped: Counter = Counter()
    held_back = []
    for f in facts:
        if not _fact_usable(f):
            held_back.append(f["id"])
            continue
        if f["metric"] == "impact":
            node = registry.node_for_fact_segment(f["context_segment"]) or TOTAL
        else:
            node = registry.node_for_fact_segment(f["segment"])
        if node is None:
            if f["segment"] is not None:
                unmapped[f["segment"]] += 1
            continue
        facts_by_node[node].append(f)
    for lst in facts_by_node.values():
        lst.sort(key=_fact_sort_key)

    trajectory = build_trajectory(facts, signals, registry, call_label)
    traj_by_node: dict[str, list[str]] = defaultdict(list)
    for row in trajectory:
        traj_by_node[row["node"]].append(row["key"])

    # ---- per-node blocks (registry order) ---------------------------------------------------------
    taken: list[dict] = []
    blocks = []
    for node in registry.nodes:
        home = [s for s in usable if s["home"] == node.id]
        mentions = [s for s in signals if node.id in s["segments"]]
        stance = Counter(s["stance"] for s in usable if node.id in s["segments"] and s["stance"])
        top_level = node.parent is None
        caps = _BUCKET_CAP_TOP if top_level else _BUCKET_CAP_SUB
        picked = _select(home, caps, _NODE_CAP_TOP if top_level else _NODE_CAP_SUB, taken)
        taken += picked
        blocks.append({
            "id": node.id, "name": node.name, "dimension": node.dimension, "parent": node.parent,
            "depth": registry.depth(node.id), "fact_ids": [f["id"] for f in facts_by_node.get(node.id, [])],
            "signal_ids": [s["id"] for s in picked], "n_home_signals": len(home), "n_mentions": len(mentions),
            "stance": dict(stance), "trajectory": traj_by_node.get(node.id, []),
        })

    # ---- company-wide statements (no segment in the sentence) -------------------------------------
    company_cands = [s for s in usable if s["home"] is None]
    company_picked = _select(company_cands, _COMPANY_CAP, 20, taken,
                             bucket_of=lambda s: "leadership" if "leadership" in s["topics"] else _bucket(s))
    taken += company_picked
    company = {"fact_ids": [f["id"] for f in facts_by_node.get(TOTAL, [])],
               "signal_ids": [s["id"] for s in company_picked], "trajectory": traj_by_node.get(TOTAL, [])}

    tone = build_tone(signals)
    selected_ids = [s["id"] for s in taken]
    surfaced_ids = list(dict.fromkeys(selected_ids + tone["notable_confident"] + tone["notable_cautious"]
                                      + tone["withheld"] + tone["revisions"]))
    scoreboard = build_scoreboard(facts, registry, blocks, trajectory, call_label)

    # ---- coverage ------------------------------------------------------------------------------
    node_ids = registry.ids()
    discussed = [b["id"] for b in blocks if b["n_mentions"] or b["fact_ids"]]
    coverage = {
        "registry": "curated" if registry.curated else "auto (no segments/<TICKER>.json)",
        "discussed": discussed,
        "not_discussed": [n for n in node_ids if n not in discussed],
        "facts_without_commentary": [b["id"] for b in blocks if b["fact_ids"] and not b["n_mentions"]],
        "commentary_without_facts": [b["id"] for b in blocks if b["n_mentions"] and not b["fact_ids"]],
        "unmapped_fact_segments": dict(unmapped),
        "held_back_low_confidence_facts": held_back,
    }

    checks = _checks(signals, blocks, company, trajectory, facts, registry, coverage, sig_by_id)
    stats = {
        "signals": len(signals), "signals_usable": len(usable), "selected": len(selected_ids),
        "by_topic": dict(Counter(t for s in signals for t in s["topics"])),
        "by_horizon": dict(Counter(s["horizon"] for s in signals)),
        "trajectory_rows": len(trajectory), "blocks_with_content": sum(1 for b in blocks if b["fact_ids"] or b["signal_ids"]),
        "checks_failed": sum(1 for c in checks if c["status"] == "fail"),
        "checks_warn": sum(1 for c in checks if c["status"] == "warn"),
    }
    return {"meta": meta, "headline": _headline(facts, trajectory, tone, call_label), "scoreboard": scoreboard,
            "company": company, "blocks": blocks, "trajectory": trajectory, "tone": tone, "signals": signals,
            "selected_signal_ids": selected_ids, "surfaced_signal_ids": surfaced_ids, "coverage": coverage,
            "checks": checks, "stats": stats}


def _checks(signals, blocks, company, trajectory, facts, registry, coverage, sig_by_id) -> list[dict]:
    checks = []

    def add(name, ok, detail, warn=False):
        checks.append({"name": name, "status": "pass" if ok else ("warn" if warn else "fail"), "detail": detail})

    bad = [s["id"] for s in signals if not s["verified"]]
    add("signals_verified", not bad, "every signal sentence matches the transcript at its offsets and was said by management"
        if not bad else f"{len(bad)} signals failed verification: {bad[:5]}")
    shown = [i for b in blocks for i in b["signal_ids"]] + company["signal_ids"]
    dup = [i for i, n in Counter(shown).items() if n > 1]
    add("no_duplicate_quotes", not dup, "each sentence appears once in the snapshot" if not dup else f"repeated: {dup[:5]}")
    low = [i for i in shown if sig_by_id[i]["confidence"] == "low"]
    add("no_low_confidence_quotes", not low, "no low-confidence signal is shown" if not low else f"low-confidence shown: {low[:5]}")
    fact_ids = {f["id"] for f in facts}
    missing = []
    for row in trajectory:
        for src in (row["reported"] or {}).get("fact_id"), (row["guided"] or {}).get("id"):
            if src and src.count("-S") == 0 and src not in fact_ids:
                missing.append(src)
    add("trajectory_sources_exist", not missing, "every trajectory input resolves to a ledger fact or a signal"
        if not missing else f"unknown sources: {missing}")
    conflicts = _total_revenue_conflicts(facts)
    add("total_revenue_consistent", not conflicts,
        "no conflicting total-company revenue figures in the ledger" if not conflicts else
        f"the ledger flagged conflicting total-company revenue figures {conflicts}: a segment figure may be filed as the "
        f"company total (sentences like 'Revenue was...' inside a segment paragraph). Check the source sentences before "
        f"quoting total revenue from this call.", warn=True)
    add("segment_registry", registry.curated,
        "curated business map in use" if registry.curated else
        "no segments/<TICKER>.json: segments were auto-discovered from facts only, so sub-segments/geographies are not broken out",
        warn=True)
    add("fact_segments_mapped", not coverage["unmapped_fact_segments"],
        "every ledger segment maps to a registry node" if not coverage["unmapped_fact_segments"]
        else f"ledger segments missing from the registry (facts not shown per-segment): {coverage['unmapped_fact_segments']}",
        warn=True)
    if coverage["held_back_low_confidence_facts"]:
        checks.append({"name": "facts_held_back", "status": "warn",
                       "detail": f"{len(coverage['held_back_low_confidence_facts'])} low-confidence ledger facts are not shown"})
    return checks

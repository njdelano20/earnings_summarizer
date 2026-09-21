"""
writer.py
Writes summaries to disk:
  <stem>_summary.md   human-readable report (fact tables, quality checks, key points)
  <stem>_facts.json   the full machine-readable fact set (what a database load would consume)
"""

import json
from collections import Counter
from pathlib import Path

from periods import title_suffix


def fmt_number(v: float, unit: str) -> str:
    if unit == "USD":
        a = abs(v)
        if a >= 1e9:
            return f"${v / 1e9:,.2f}B".replace(".00B", "B")
        if a >= 1e6:
            return f"${v / 1e6:,.1f}M".replace(".0M", "M")
        return f"${v:,.0f}"
    if unit == "USD_per_share":
        return f"${v:,.2f}/sh"
    if unit == "pct":
        return f"{v:g}%"
    if unit == "bps":
        return f"{v:g} bps"
    if unit == "pp":
        return f"{v:g} pp"
    return f"{v:g}"


def fmt_value(f: dict) -> str:
    if f.get("descriptor"):
        return f'"{f["descriptor"]}"'
    if f.get("value") is None:
        return "-"
    text = fmt_number(f["value"], f["unit"])
    if f.get("value_high") is not None:
        text += f" to {fmt_number(f['value_high'], f['unit'])}"
    if f.get("qualifier"):
        text = f"{f['qualifier']} {text}"
    return text


def fmt_changes(f: dict) -> str:
    parts = []
    for c in f.get("changes", []):
        if c["unit"] == "USD":
            piece = ("+" if c["value"] >= 0 else "-") + fmt_number(abs(c["value"]), "USD")
        else:
            piece = f"{c['value']:+g}{'%' if c['unit'] == 'pct' else ' ' + c['unit']}"
        if c.get("basis"):
            piece += f" {c['basis']}"
        if c.get("constant_currency"):
            piece += " cc"
        parts.append(piece)
    return ", ".join(parts)


def _cell(s) -> str:
    return str(s if s is not None else "-").replace("|", "\\|").replace("\n", " ")


def _metric_label(f: dict) -> str:
    label = f["metric"].replace("_", " ")
    if f["stat"] in ("growth", "change"):
        label += f" {f['stat']}"
        if f.get("basis"):
            label += f" ({f['basis']})"
    if f.get("accounting"):
        label += f" [{f['accounting']}]"
    if f.get("currency_basis"):
        label += " [const. currency]"
    return label


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


def facts_markdown(result: dict) -> list[str]:
    meta, facts = result["meta"], result["facts"]
    lines: list[str] = []
    title = " ".join(str(x) for x in (meta.get("ticker"), f"Q{meta['fiscal_quarter']}" if meta.get("fiscal_quarter") else None,
                                      meta.get("fiscal_year")) if x)
    if title:
        title += title_suffix(meta)          # "(calendar 2026Q1; quarter ended Jan 2026)" when the fiscal label differs
    lines += [f"**Call:** {title or meta.get('source_file')}  ", f"**Reported period label in text:** {meta.get('reported_period_label')}  ",
              f"**Source:** {meta.get('source')}  ", ""]

    reported = [f for f in facts if f["kind"] == "reported" and f["metric"] != "impact"]
    lines.append("## Reported Figures")
    if reported:
        lines += _table(["Metric", "Segment", "Period", "Value", "Change", "Speaker", "Conf."],
                        [[_metric_label(f), f["segment"], f["period"], fmt_value(f), fmt_changes(f),
                          f["speaker"], f["confidence"]] for f in reported])
    else:
        lines.append("_No reported figures extracted._")
    lines.append("")

    guidance = [f for f in facts if f["kind"] in ("guidance", "declared") and f["metric"] != "impact"]
    lines.append("## Guidance and Declarations")
    if guidance:
        lines += _table(["Kind", "Metric", "Segment", "Period", "Outlook", "Speaker", "Conf."],
                        [[f["kind"], _metric_label(f), f["segment"], f["period"], fmt_value(f),
                          f["speaker"], f["confidence"]] for f in guidance])
    else:
        lines.append("_No guidance extracted._")
    lines.append("")

    impacts = [f for f in facts if f["metric"] == "impact"]
    if impacts:
        lines.append("## Drivers and Adjustments")
        lines += _table(["Kind", "Driver", "Direction", "Size", "Applies to", "Period", "Conf."],
                        [[f["kind"], f["driver"], f["direction"], fmt_value(f),
                          f"{f['context_metric']}/{f['context_segment']}" if f["context_metric"] else "unresolved",
                          f["period"], f["confidence"]] for f in impacts])
        lines.append("")

    stats = result["stats"]
    lines.append("## Data Quality")
    lines.append(f"- {stats['facts']} facts: {stats['by_confidence']} confidence; "
                 f"{stats['verified']} verified against source text, {stats['unverified']} failed verification.")
    for c in result["checks"]:
        lines.append(f"- {c['status'].upper()} `{c['name']}`: {c['detail']}")
    review = [f for f in facts if f["confidence"] != "high"]
    if review:
        lines.append(f"- {len(review)} facts below high confidence need review before loading into the database "
                     f"(see the `flags` field in the JSON).")
    reasons = Counter(u["reason"] for u in result["unclaimed"])
    lines.append(f"- {stats['unclaimed_figures']} numeric figures in management speech were not captured as facts "
                 f"({dict(reasons)}); full list in the JSON `unclaimed` field.")
    lines.append("")
    return lines


def write_summary(filename: str, summary: dict, output_folder: str = "output",
                  facts_result: dict | None = None) -> str:
    """
    Writes one summary to a .md file named after the source transcript.
    Returns the path written to.
    """
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    out_path = Path(output_folder) / f"{stem}_summary.md"

    lines = [f"# Summary: {filename}", ""]

    if facts_result is not None:
        lines += facts_markdown(facts_result)
    else:
        lines.append("## Key Metrics")
        any_metrics = False
        for label, values in summary.get("metrics", {}).items():
            if values:
                any_metrics = True
                lines.append(f"**{label.replace('_', ' ').title()}:**")
                lines += [f"- {v}" for v in values]
        if not any_metrics:
            lines.append("_No metrics matched._")
        lines.append("")

    lines.append("## Key Points (legacy keyword-based placeholder; not yet quality-gated)")
    key_sentences = summary.get("key_sentences", [])
    if key_sentences:
        lines += [f"- {s}" for s in key_sentences]
    else:
        lines.append("_No key sentences extracted._")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


def write_facts_json(filename: str, facts_result: dict, output_folder: str = "output") -> str:
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    out_path = Path(output_folder) / f"{Path(filename).stem}_facts.json"
    out_path.write_text(json.dumps(facts_result, indent=1, ensure_ascii=False), encoding="utf-8")
    return str(out_path)


# --------------------------------------------------------------------------- #
# Business snapshot (momentum / product / competition / outlook / management stance)
# --------------------------------------------------------------------------- #

_GLYPH = {"positive": "▲", "negative": "▼", "mixed": "◆", "neutral": "●"}
_BUCKET_LABEL = {"momentum": "Momentum", "product": "Product", "competition": "Competition", "pressure": "Pressure",
                 "strategy": "Strategy", "outlook": "Outlook", "leadership": "Leadership"}
_BUCKET_ORDER = ["momentum", "product", "competition", "pressure", "strategy", "outlook", "leadership"]
_DIM_TITLES = {"segment": "Business segments", "geography": "Geographies", "customer": "Customer groups",
               "initiative": "Strategic initiatives", "driver": "Cost, supply, currency and regulatory drivers"}
_QUOTE_MAX = 380


def _quote(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _QUOTE_MAX else text[:_QUOTE_MAX].rsplit(" ", 1)[0] + " ..."


def _bucket_of(sig: dict) -> str:
    if sig["horizon"] == "forward":
        return "outlook"
    if "leadership" in sig["topics"]:
        return "leadership"
    for t in sig["topics"]:
        if t in _BUCKET_LABEL:
            return t
    return "momentum"


def _signal_line(sig: dict) -> str:
    bucket = _bucket_of(sig)
    tags = [_BUCKET_LABEL[bucket]]
    if sig.get("revision"):
        tags.append(sig["revision"])
    if sig.get("withheld"):
        tags.append("withheld")
    elif sig.get("stance") in ("confident", "cautious"):
        tags.append(sig["stance"])
    refs = f" facts: {', '.join(f'`{i}`' for i in sig['fact_ids'])}" if sig["fact_ids"] else ""
    ctx = " (segment inferred from nearby sentence)" if "segment_from_context" in sig["flags"] else ""
    return (f"- {_GLYPH.get(sig['direction'], '●')} **{' · '.join(tags)}**: \"{_quote(sig['sentence'])}\" "
            f"-- {sig['speaker']}, {sig['section']} `{sig['id']}`{refs}{ctx}")


def range_text(lo, hi) -> str:
    """'9% to 11%', '35%', 'at least 35%' (a floor), 'at most 5%' (a ceiling)."""
    if lo is not None and hi is not None:
        return fmt_number(lo, "pct") if lo == hi else f"{fmt_number(lo, 'pct')} to {fmt_number(hi, 'pct')}"
    if lo is not None:
        return f"at least {fmt_number(lo, 'pct')}"
    return f"at most {fmt_number(hi, 'pct')}"


def _fact_label(f: dict) -> str:
    if f["metric"] != "impact":
        return _metric_label(f)
    label = f"{f['driver'] or 'unclassified'} impact"
    if f.get("context_metric"):
        label += f" on {f['context_metric'].replace('_', ' ')}"
    if f.get("direction"):
        label += f" ({f['direction']})"
    return label


def _scoreboard_table(snapshot: dict) -> list[str]:
    verb = {"up": "▲ accelerating", "down": "▼ decelerating", "flat": "◆ steady"}
    rows = []
    for r in snapshot["scoreboard"]:
        ids = list(dict.fromkeys(x["fact_id"] for x in (r["revenue"], r["growth"], r["gross_margin"]) if x))
        growth = ""
        if r["growth"]:
            growth = f"{r['growth']['value']:+g}%" + (" cc" if r["growth"].get("currency_basis") == "constant" else "")
        outlook = ""
        if r["outlook"]:
            o = r["outlook"]
            if o["guided_lo"] is not None or o["guided_hi"] is not None:
                guide = range_text(o["guided_lo"], o["guided_hi"])
                if o.get("guided_descriptor"):
                    guide = f"{o['guided_descriptor']}: {guide}"
            else:
                guide = "stated direction"
            outlook = f"{verb.get(o['direction'], o['direction'])} ({guide})"
        st = r["stance"]
        wording = " / ".join(f"{st[k]} {k[:4]}." for k in ("confident", "cautious", "mixed") if st.get(k))
        rows.append([("↳ " * r["depth"]) + r["name"],
                     fmt_number(r["revenue"]["value"], "USD") if r["revenue"] else "",
                     growth, f"{r['gross_margin']['value']:g}%" if r["gross_margin"] else "", outlook, wording,
                     "" if r["node"] == "total" else r["n_mentions"],
                     ", ".join(f"`{i}`" for i in ids)])
    return _table(["Segment", "Revenue", "Growth (yoy)", "Gross margin", "Outlook", "Mgmt wording", "Mentions", "Facts"], rows)


def _fact_table(fact_ids: list[str], facts_by_id: dict) -> list[str]:
    rows = []
    for i in fact_ids:
        f = facts_by_id[i]
        rows.append([f["kind"], _fact_label(f), f["period"], fmt_value(f), fmt_changes(f),
                     f["confidence"] if f["confidence"] != "high" else "", f"`{f['id']}`"])
    return _table(["Type", "Metric", "Period", "Value", "Change", "Conf.", "Fact"], rows) if rows else []


def _stance_line(stance: dict) -> str | None:
    if not stance:
        return None
    parts = [f"{stance[k]} {k}" for k in ("confident", "cautious", "mixed") if stance.get(k)]
    return f"_Management wording on this area: {', '.join(parts)} statements (lexical count)._"


def _trajectory_rows(snapshot: dict) -> list[list]:
    verb = {("growth", "up"): "▲ accelerating", ("growth", "down"): "▼ decelerating", ("growth", "flat"): "◆ steady",
            ("level", "up"): "▲ higher", ("level", "down"): "▼ lower", ("level", "flat"): "◆ similar"}
    rows = []
    for r in snapshot["trajectory"]:
        rep, g = r["reported"], r["guided"]
        rep_txt = (f"{rep['value']:g}%" + (f" ({rep['period']})" if rep.get("period") else "")) if rep else "n/a"
        if g["lo"] is not None or g["hi"] is not None:
            guide = range_text(g["lo"], g["hi"])
            if g.get("descriptor"):
                guide = f'"{g["descriptor"]}" ({guide})'
        elif g.get("descriptor"):
            guide = f'"{g["descriptor"][:90]}{"..." if len(g["descriptor"]) > 90 else ""}" (stated, not numeric)'
        else:
            guide = f'"{g.get("direction_word")}" (stated direction)'
        if g.get("period"):
            guide += f" [{g['period']}]"
        src = f"`{g['id']}`" + (f" / `{rep['fact_id']}`" if rep else "")
        rows.append([r["name"], r["metric"].replace("_", " ") + (" growth" if r["kind"] == "growth" else ""), rep_txt, guide,
                     verb.get((r["kind"], r["direction"]), r["direction"]),
                     f"{r['delta_pp']:+g} pp" if r["delta_pp"] is not None else "-", src, r["confidence"]])
    return rows


def snapshot_markdown(snapshot: dict, facts_result: dict) -> list[str]:
    facts_by_id = {f["id"]: f for f in facts_result["facts"]}
    sigs = {s["id"]: s for s in snapshot["signals"]}
    meta = snapshot["meta"]
    title = " ".join(str(x) for x in (meta.get("ticker"), f"Q{meta['fiscal_quarter']}" if meta.get("fiscal_quarter") else None,
                                      meta.get("fiscal_year")) if x)
    title = title + title_suffix(meta) if title else str(meta.get("source_file"))
    out = [f"# {title} - Business Snapshot", "",
           f"_Call period as the call itself describes it: {meta.get('reported_period_label')}. Source: {meta.get('source')}. "
           f"Every figure is a verified ledger fact (id shown); every statement is a verbatim management sentence (id shown). "
           f"▲ positive news, ▼ negative, ◆ mixed, ● neutral._", ""]

    out.append("## At a glance")
    for h in snapshot["headline"]:
        refs = f" {', '.join(f'`{i}`' for i in h['fact_ids'])}" if h["fact_ids"] else ""
        out.append(f"- {h['text']}{refs}")
    out.append("")

    out.append("## Segment scoreboard")
    out.append("_One row per business segment: latest reported revenue, growth and margin, how management says it is trending, "
               "and how many management statements touched it. Blank = the call gave no figure._")
    out.append("")
    out += _scoreboard_table(snapshot)
    out.append("")

    out.append("## Company-wide picture")
    if snapshot["company"]["fact_ids"]:
        out += _fact_table(snapshot["company"]["fact_ids"], facts_by_id)
        out.append("")
    company_sigs = sorted((sigs[i] for i in snapshot["company"]["signal_ids"]),
                          key=lambda s: (_BUCKET_ORDER.index(_bucket_of(s)), s["turn_index"], s["sentence_index"]))
    out += [_signal_line(s) for s in company_sigs] or ["_No company-wide statements selected._"]
    out.append("")

    out.append("## Trajectory: where each line is versus where management says it is going")
    rows = _trajectory_rows(snapshot)
    if rows:
        out += _table(["Area", "Metric", "Reported", "Guided / stated", "Read", "Gap", "Source", "Conf."], rows)
        out.append("")
        out.append("_Read = the reported figure compared with the guided range (outside the range = accelerating/decelerating, "
                   "inside = steady). Sources shown as a signal id (S...) were read from the sentence and are not in the "
                   "fact ledger yet._")
    else:
        out.append("_No reported-versus-guided pairs could be built from this call._")
    out.append("")

    out.append("## Business breakdown")
    out.append("_Every segment, geography, customer group, initiative and cost driver the call touched. Areas with no figures and "
               "no selected commentary are listed under Coverage._")
    out.append("")
    by_dim: dict[str, list[dict]] = {}
    for b in snapshot["blocks"]:
        by_dim.setdefault(b["dimension"], []).append(b)
    for dim in ("segment", "geography", "customer", "initiative", "driver"):
        blocks = [b for b in by_dim.get(dim, []) if b["fact_ids"] or b["signal_ids"]]
        if not blocks:
            continue
        out.append(f"### {_DIM_TITLES[dim]}")
        out.append("")
        for b in blocks:
            out.append(f"{'#' * min(6, 4 + b['depth'])} {b['name']}")
            out.append("")
            if b["fact_ids"]:
                out += _fact_table(b["fact_ids"], facts_by_id)
                out.append("")
            for key in b["trajectory"]:
                r = next(t for t in snapshot["trajectory"] if t["key"] == key)
                out.append(f"- Trajectory: **{r['direction']}** ({r['metric'].replace('_', ' ')}), see trajectory table.")
            block_sigs = sorted((sigs[i] for i in b["signal_ids"]),
                                key=lambda s: (_BUCKET_ORDER.index(_bucket_of(s)), s["turn_index"], s["sentence_index"]))
            out += [_signal_line(s) for s in block_sigs]
            sl = _stance_line(b["stance"])
            if sl:
                out.append(sl)
            out.append("")

    tone = snapshot["tone"]
    out.append("## Management's stance toward the future")
    out.append(f"_{tone['method']}._")
    out.append("")
    out += _table(["Section", "Confident", "Cautious", "Mixed", "Withheld guidance", "Index (-1..+1)"],
                  [[sec, v["confident"], v["cautious"], v["mixed"], v["withheld"], v["index"] if v["index"] is not None else "-"]
                   for sec, v in tone["sections"].items()])
    out.append("")
    if tone["speakers"]:
        out += _table(["Speaker", "Confident", "Cautious", "Mixed"],
                      [[k, v.get("confident", 0), v.get("cautious", 0), v.get("mixed", 0)] for k, v in tone["speakers"].items()])
        out.append("")
    for heading, ids in (("Most confident", tone["notable_confident"]), ("Most cautious or hedged", tone["notable_cautious"]),
                         ("Declined to give guidance", tone["withheld"]), ("Changes to management's own view", tone["revisions"])):
        if ids:
            out.append(f"**{heading}**")
            out += [_signal_line(sigs[i]) for i in ids]
            out.append("")

    cov, stats = snapshot["coverage"], snapshot["stats"]
    names = {b["id"]: b["name"] for b in snapshot["blocks"]}
    out.append("## Coverage and data quality")
    out.append(f"- Business map: {cov['registry']}. {stats['signals']} management statements analysed "
               f"({stats['signals_usable']} usable, {stats['selected']} shown above); by topic {stats['by_topic']}.")
    if cov["facts_without_commentary"]:
        out.append("- Figures but no management commentary: " + ", ".join(names[i] for i in cov["facts_without_commentary"]))
    if cov["commentary_without_facts"]:
        out.append("- Commentary but no ledger figures: " + ", ".join(names[i] for i in cov["commentary_without_facts"]))
    if cov["not_discussed"]:
        out.append("- Not discussed on this call: " + ", ".join(names[i] for i in cov["not_discussed"]))
    if cov["held_back_low_confidence_facts"]:
        out.append(f"- {len(cov['held_back_low_confidence_facts'])} low-confidence ledger facts were held back from this page: "
                   + ", ".join(f"`{i}`" for i in cov["held_back_low_confidence_facts"]))
    for c in snapshot["checks"]:
        out.append(f"- {c['status'].upper()} `{c['name']}`: {c['detail']}")
    out.append("")
    return out


def write_snapshot(filename: str, snapshot: dict, facts_result: dict, output_folder: str = "output") -> tuple[str, str]:
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    md_path = Path(output_folder) / f"{stem}_snapshot.md"
    md_path.write_text("\n".join(snapshot_markdown(snapshot, facts_result)), encoding="utf-8")
    json_path = Path(output_folder) / f"{stem}_snapshot.json"
    json_path.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")
    return str(md_path), str(json_path)

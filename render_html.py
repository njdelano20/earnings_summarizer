"""
render_html.py
Turns every output/<call>_snapshot.json (+ its _facts.json) into one self-contained reading page,
output/snapshots.html: a tab per call, segment scoreboard, reported-vs-guided trajectory, collapsible
per-segment blocks with the ledger facts and verbatim management quotes, and management's stance.

    py render_html.py

Calls with no gold set in gold/snapshot/ are labelled as not yet validated. The page is content-only
(no <html>/<body>), so it can be published as an Artifact as-is; browsers open it fine too.
"""

import html
import json
import sys
from pathlib import Path

from periods import title_suffix
from writer import (_BUCKET_LABEL, _BUCKET_ORDER, _DIM_TITLES, _bucket_of, _fact_label, fmt_changes, fmt_number,
                    fmt_value, range_text)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
_GLYPH = {"positive": "▲", "negative": "▼", "mixed": "◆", "neutral": "●"}
_DIR_CLASS = {"positive": "pos", "negative": "neg", "mixed": "mix", "neutral": "neu"}
_VERB = {("growth", "up"): ("▲ accelerating", "pos"), ("growth", "down"): ("▼ decelerating", "neg"),
         ("growth", "flat"): ("◆ steady", "neu"), ("level", "up"): ("▲ higher", "pos"),
         ("level", "down"): ("▼ lower", "neg"), ("level", "flat"): ("◆ similar", "neu")}


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def ids(items) -> str:
    return " ".join(f'<code class="id">{esc(i)}</code>' for i in items)


def pill(cls: str, text: str) -> str:
    return f'<span class="pill p-{cls}">{esc(text)}</span>'


def table(head: list[str], rows: list[list[str]], cls: str = "") -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def fact_table(fact_ids: list[str], facts: dict) -> str:
    rows = []
    for i in fact_ids:
        f = facts[i]
        conf = "" if f["confidence"] == "high" else pill("mix" if f["confidence"] == "medium" else "neg", f["confidence"])
        kind = pill("acc" if f["kind"] != "reported" else "neu", f["kind"])
        rows.append([kind, esc(_fact_label(f)), esc(f["period"] or "-"), f'<span class="num">{esc(fmt_value(f))}</span>',
                     f'<span class="num">{esc(fmt_changes(f))}</span>', conf, f'<code class="id">{esc(f["id"])}</code>'])
    return table(["Type", "Metric", "Period", "Value", "Change", "Trust", "Fact"], rows, "facts") if rows else ""


def quote_li(sig: dict) -> str:
    bucket = _bucket_of(sig)
    tags = [f'<span class="tag t-{bucket}">{esc(_BUCKET_LABEL[bucket])}</span>']
    if sig.get("revision"):
        tags.append(f'<span class="tag t-rev">{esc(sig["revision"])}</span>')
    if sig.get("withheld"):
        tags.append('<span class="tag t-hedge">withheld</span>')
    elif sig.get("stance") in ("confident", "cautious"):
        tags.append(f'<span class="tag t-{sig["stance"]}">{esc(sig["stance"])}</span>')
    fx = f' <span class="dim">facts</span> {ids(sig["fact_ids"])}' if sig["fact_ids"] else ""
    ctx = ' <span class="dim">(segment inferred from a nearby sentence)</span>' if "segment_from_context" in sig["flags"] else ""
    return (f'<li class="q" data-bucket="{bucket}"><span class="dir d-{_DIR_CLASS[sig["direction"]]}" '
            f'title="{esc(sig["direction"])} news">{_GLYPH.get(sig["direction"], "●")}</span>'
            f'<div><div class="tags">{"".join(tags)}</div><blockquote>{esc(sig["sentence"])}</blockquote>'
            f'<div class="meta">{esc(sig["speaker"])} · {esc(sig["section"])}'
            f'<span class="src"> · <code class="id">{esc(sig["id"])}</code>{fx}</span>{ctx}</div></div></li>')


def quotes_ul(sigs: list[dict]) -> str:
    sigs = sorted(sigs, key=lambda s: (_BUCKET_ORDER.index(_bucket_of(s)), s["turn_index"], s["sentence_index"]))
    return '<ul class="quotes">' + "".join(quote_li(s) for s in sigs) + "</ul>" if sigs else ""


def stance_bar(conf: int, caut: int) -> str:
    total = conf + caut
    if not total:
        return '<span class="dim">no stance cues</span>'
    return (f'<span class="bar" role="img" aria-label="{conf} confident, {caut} cautious">'
            f'<i class="b-conf" style="width:{100 * conf / total:.0f}%"></i>'
            f'<i class="b-caut" style="width:{100 * caut / total:.0f}%"></i></span>')


def call_title(meta: dict) -> str:
    q = f"Q{meta['fiscal_quarter']} {meta['fiscal_year']}" if meta.get("fiscal_quarter") else ""
    return f"{meta.get('ticker') or meta.get('source_file')} {q}{title_suffix(meta) if q else ''}".strip()


def call_section(key: str, snap: dict, facts_result: dict, has_gold: bool) -> str:
    facts = {f["id"]: f for f in facts_result["facts"]}
    sigs = {s["id"]: s for s in snap["signals"]}
    meta = snap["meta"]
    out = [f'<section class="call" id="call-{esc(key)}" data-call="{esc(key)}" hidden>']

    # ---- masthead + trust strip --------------------------------------------------------------
    checks = snap["checks"]
    n_pass = sum(c["status"] == "pass" for c in checks)
    warns = [c for c in checks if c["status"] != "pass"]
    out.append(f'<div class="masthead"><h2>{esc(call_title(meta))}</h2>'
               f'<p class="dim">The call calls its own period the <b>{esc(meta.get("reported_period_label"))}</b> · '
               f'source: {esc(meta.get("source"))} · {snap["stats"]["signals"]} management statements analysed, '
               f'{snap["stats"]["selected"]} shown</p></div>')
    if not has_gold:
        out.append('<div class="note note-warn"><b>Not yet validated.</b> This call has no gold set in <code>gold/snapshot/</code>, '
                   'so the ranking of which sentences are shown, and the segment map, have only been checked on Apple and IBM. '
                   'Treat the selection as a draft and the figures as what the ledger extracted, with its trust level.</div>')
    if warns:
        items = "".join(f"<li><code>{esc(c['name'])}</code> {esc(c['detail'])}</li>" for c in warns)
        out.append(f'<div class="note note-warn"><b>{n_pass} checks passed, {len(warns)} need attention.</b><ul>{items}</ul></div>')
    else:
        out.append(f'<div class="note note-ok"><b>All {n_pass} checks passed.</b> Every quote matches the transcript at its '
                   'offsets and was said by management; no low-confidence figure is shown.</div>')

    # ---- at a glance --------------------------------------------------------------------------
    lis = "".join(f'<li>{esc(h["text"])} {ids(h["fact_ids"])}</li>' for h in snap["headline"])
    out.append(f'<h3 id="{key}-glance">At a glance</h3><ul class="glance">{lis}</ul>')

    # ---- scoreboard ---------------------------------------------------------------------------
    rows = []
    for r in snap["scoreboard"]:
        name = f'<span class="ind i{r["depth"]}">{esc(r["name"])}</span>'
        growth = ""
        if r["growth"]:
            v = r["growth"]["value"]
            growth = f'<span class="num {"neg" if v < 0 else ""}">{v:+g}%{" cc" if r["growth"].get("currency_basis") == "constant" else ""}</span>'
        outlook = ""
        if r["outlook"]:
            o = r["outlook"]
            label, cls = _VERB[("growth", o["direction"])] if ("growth", o["direction"]) in _VERB else (o["direction"], "neu")
            guide = range_text(o["guided_lo"], o["guided_hi"]) if (o["guided_lo"] is not None or o["guided_hi"] is not None) else "stated"
            outlook = f'{pill(cls, label)} <span class="dim">guided {esc(guide)}</span>'
        st = r["stance"]
        rows.append([name, f'<span class="num">{esc(fmt_number(r["revenue"]["value"], "USD")) if r["revenue"] else ""}</span>',
                     growth, f'<span class="num">{r["gross_margin"]["value"]:g}%</span>' if r["gross_margin"] else "", outlook,
                     stance_bar(st.get("confident", 0), st.get("cautious", 0)) if st else "",
                     "" if r["node"] == "total" else str(r["n_mentions"])])
    out.append('<h3 id="%s-score">Segment scoreboard</h3><p class="dim">One row per business segment. Blank means the call gave no '
               'figure. The bar is management wording: confident (blue) versus cautious (amber).</p>' % key)
    out.append(table(["Segment", "Revenue", "Growth yoy", "Gross margin", "Where it is heading", "Wording", "Mentions"], rows, "score"))

    # ---- trajectory ---------------------------------------------------------------------------
    out.append(f'<h3 id="{key}-traj">Trajectory: reported versus guided</h3>')
    if snap["trajectory"]:
        trows = []
        for r in snap["trajectory"]:
            rep, g = r["reported"], r["guided"]
            rep_t = f'<span class="num">{rep["value"]:g}%</span> <span class="dim">{esc(rep.get("period") or "")}</span>' if rep else '<span class="dim">no figure</span>'
            if g["lo"] is not None or g["hi"] is not None:
                gt = f'<span class="num">{esc(range_text(g["lo"], g["hi"]))}</span>'
                if g.get("descriptor"):
                    gt = f'“{esc(g["descriptor"])}” ' + gt
            elif g.get("descriptor"):
                gt = f'“{esc(g["descriptor"][:80])}…”'
            else:
                gt = f'“{esc(g.get("direction_word"))}” <span class="dim">(stated direction)</span>'
            if g.get("period"):
                gt += f' <span class="dim">{esc(g["period"])}</span>'
            label, cls = _VERB.get((r["kind"], r["direction"]), (r["direction"], "neu"))
            gap = f'<span class="num">{r["delta_pp"]:+g} pp</span>' if r["delta_pp"] is not None else ""
            src = f'<code class="id">{esc(g["id"])}</code>' + (f' <code class="id">{esc(rep["fact_id"])}</code>' if rep else "")
            trows.append([esc(r["name"]), esc(r["metric"].replace("_", " ") + (" growth" if r["kind"] == "growth" else "")),
                          rep_t, gt, pill(cls, label), gap, src, pill("mix", "medium") if r["confidence"] != "high" else ""])
        out.append(table(["Area", "Metric", "Reported", "Guided or stated", "Read", "Gap", "Source", "Trust"], trows))
        out.append('<p class="dim">Read compares the reported figure with the guided range: outside it means accelerating or '
                   'decelerating, inside means steady. A source id starting with S was read from the sentence and is not in the fact ledger yet.</p>')
    else:
        out.append('<p class="dim">No reported-versus-guided pair could be built from this call.</p>')

    # ---- company-wide -------------------------------------------------------------------------
    out.append(f'<h3 id="{key}-company">Company-wide picture</h3>')
    out.append(fact_table(snap["company"]["fact_ids"], facts))
    out.append(quotes_ul([sigs[i] for i in snap["company"]["signal_ids"]]))

    # ---- breakdown ----------------------------------------------------------------------------
    out.append(f'<h3 id="{key}-break">Business breakdown</h3><p class="dim">Every segment, geography, customer group, '
               'initiative and cost driver the call touched. Use the filters to focus on one kind of statement.</p>')
    by_dim: dict[str, list[dict]] = {}
    for b in snap["blocks"]:
        if b["fact_ids"] or b["signal_ids"]:
            by_dim.setdefault(b["dimension"], []).append(b)
    traj = {r["key"]: r for r in snap["trajectory"]}
    score = {r["node"]: r for r in snap["scoreboard"]}
    for dim in ("segment", "geography", "customer", "initiative", "driver"):
        if dim not in by_dim:
            continue
        out.append(f'<h4 class="dim-h">{esc(_DIM_TITLES[dim])}</h4>')
        for b in by_dim[dim]:
            chips = []
            sc = score.get(b["id"])
            if sc and sc["revenue"]:
                chips.append(f'<span class="chip num">{esc(fmt_number(sc["revenue"]["value"], "USD"))}</span>')
            if sc and sc["growth"]:
                v = sc["growth"]["value"]
                chips.append(f'<span class="chip num {"neg" if v < 0 else ""}">{v:+g}%</span>')
            for k in b["trajectory"]:
                label, cls = _VERB.get((traj[k]["kind"], traj[k]["direction"]), (traj[k]["direction"], "neu"))
                chips.append(pill(cls, label))
            out.append(f'<details class="node depth{min(b["depth"], 2)}" open><summary><span class="nm">{esc(b["name"])}</span>'
                       f'<span class="chips">{"".join(chips)}</span></summary>'
                       f'{fact_table(b["fact_ids"], facts)}{quotes_ul([sigs[i] for i in b["signal_ids"]])}</details>')

    # ---- stance -------------------------------------------------------------------------------
    tone = snap["tone"]
    out.append(f'<h3 id="{key}-stance">Management’s stance toward the future</h3><p class="dim">{esc(tone["method"])}.</p>')
    srows = [[esc(sec), f'<span class="num">{v["confident"]}</span>', f'<span class="num">{v["cautious"]}</span>',
              f'<span class="num">{v["withheld"]}</span>', stance_bar(v["confident"], v["cautious"]),
              f'<span class="num">{v["index"]:+.2f}</span>' if v["index"] is not None else '<span class="dim">no cues</span>']
             for sec, v in tone["sections"].items()]
    out.append(table(["Section", "Confident", "Cautious", "Declined to guide", "Balance", "Index"], srows))
    for heading, key_ in (("Most confident", "notable_confident"), ("Most cautious or hedged", "notable_cautious"),
                          ("Declined to give guidance", "withheld"), ("Changes to management’s own view", "revisions")):
        if tone[key_]:
            out.append(f'<h4>{esc(heading)}</h4>{quotes_ul([sigs[i] for i in tone[key_]])}')

    # ---- coverage -----------------------------------------------------------------------------
    cov = snap["coverage"]
    names = {b["id"]: b["name"] for b in snap["blocks"]}
    items = [f'Business map: {esc(cov["registry"])}.']
    if cov["facts_without_commentary"]:
        items.append("Figures but no commentary: " + esc(", ".join(names[i] for i in cov["facts_without_commentary"])))
    if cov["commentary_without_facts"]:
        items.append("Commentary but no ledger figures: " + esc(", ".join(names[i] for i in cov["commentary_without_facts"])))
    if cov["not_discussed"]:
        items.append("Not discussed on this call: " + esc(", ".join(names[i] for i in cov["not_discussed"])))
    if cov["held_back_low_confidence_facts"]:
        items.append(f'{len(cov["held_back_low_confidence_facts"])} low-confidence ledger facts held back from this page: '
                     + ids(cov["held_back_low_confidence_facts"]))
    if cov["unmapped_fact_segments"]:
        items.append("Ledger segments not in the business map: " + esc(", ".join(cov["unmapped_fact_segments"])))
    out.append(f'<h3 id="{key}-cov">Coverage and data quality</h3><ul class="cov">' + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
    out.append("</section>")
    return "\n".join(out)


CSS = """
:root{--bg:#f3f5f9;--surface:#fff;--surface2:#e9edf4;--line:#d3dae5;--text:#152030;--muted:#556479;--accent:#2a4a99;--accent-ink:#fff;
--pos:#17724a;--pos-bg:#e0f2e8;--neg:#ae3626;--neg-bg:#fbe6e1;--mix:#8c5f06;--mix-bg:#faefd6;--neu:#526177;--neu-bg:#e7ebf2;
--shadow:0 1px 2px rgba(20,32,48,.06)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d121a;--surface:#141b26;--surface2:#1b2432;--line:#28334a;--text:#e4e9f2;--muted:#9aa8bd;--accent:#8fb0ff;--accent-ink:#0d121a;
--pos:#5fd39a;--pos-bg:#11291f;--neg:#ff9080;--neg-bg:#341a1b;--mix:#f1c862;--mix-bg:#2f2610;--neu:#a8b5c9;--neu-bg:#212b3a;--shadow:none}}
:root[data-theme="dark"]{--bg:#0d121a;--surface:#141b26;--surface2:#1b2432;--line:#28334a;--text:#e4e9f2;--muted:#9aa8bd;--accent:#8fb0ff;--accent-ink:#0d121a;
--pos:#5fd39a;--pos-bg:#11291f;--neg:#ff9080;--neg-bg:#341a1b;--mix:#f1c862;--mix-bg:#2f2610;--neu:#a8b5c9;--neu-bg:#212b3a;--shadow:none}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font:15px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:0 64px}
.wrap{max-width:1080px;margin-inline:auto}
code,.num,.id{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}.num.neg{color:var(--neg)}
h2{font-size:1.6rem;margin:0;letter-spacing:-.01em;text-wrap:balance}h3{font-size:1.1rem;margin:2.2rem 0 .6rem;padding-top:.4rem;border-top:1px solid var(--line)}
h4{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:1.4rem 0 .4rem}
.dim-h{margin-top:1.8rem}.dim{color:var(--muted)}p{margin:.3rem 0 .6rem;max-width:70ch}
.top{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding-block:12px 0;margin-inline:-16px;padding-inline:16px}
.top .wrap{display:flex;flex-wrap:wrap;align-items:end;gap:6px 24px}
.brand{font-weight:600;font-size:.95rem;padding-bottom:10px;color:var(--muted)}
.tabs{display:flex;gap:2px;flex-wrap:wrap;max-height:136px;overflow-y:auto;padding-right:2px}
.tab{appearance:none;border:0;background:none;font:inherit;color:var(--muted);padding:9px 14px;border-bottom:3px solid transparent;cursor:pointer;font-weight:500}
.tab[aria-selected="true"]{color:var(--text);border-bottom-color:var(--accent)}.tab:hover{color:var(--text)}
.tab small{display:block;font-size:.68rem;font-weight:400;color:var(--muted)}
button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.masthead{padding-top:22px}.note{border:1px solid var(--line);background:var(--surface);border-radius:6px;padding:10px 14px;margin:14px 0;font-size:.9rem}
.note ul{margin:.4rem 0 0;padding-left:1.1rem}.note li{margin:.2rem 0}
.note-warn{background:var(--mix-bg);border-color:transparent;color:var(--text)}.note-ok{background:var(--pos-bg);border-color:transparent}
.glance{list-style:none;margin:0;padding:0;display:grid;gap:6px}.glance li{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:8px 12px;box-shadow:var(--shadow)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--surface);margin:.5rem 0 1rem}
table{border-collapse:collapse;width:100%;font-size:.86rem}th{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);text-align:left;font-weight:600;background:var(--surface2)}
th,td{padding:7px 12px;border-bottom:1px solid var(--line);vertical-align:top}tbody tr:last-child td{border-bottom:0}
.ind{display:inline-block}.i1{padding-left:14px}.i2{padding-left:28px}.i1:before,.i2:before{content:"↳ ";color:var(--muted)}
.id{font-size:.7rem;color:var(--muted);background:var(--surface2);border-radius:3px;padding:1px 4px;white-space:nowrap}
.pill{display:inline-block;font-size:.72rem;font-weight:600;border-radius:99px;padding:1px 9px;white-space:nowrap}
.p-pos{color:var(--pos);background:var(--pos-bg)}.p-neg{color:var(--neg);background:var(--neg-bg)}.p-mix{color:var(--mix);background:var(--mix-bg)}
.p-neu{color:var(--neu);background:var(--neu-bg)}.p-acc{color:var(--accent);background:var(--surface2)}
.bar{display:inline-flex;width:88px;height:8px;border-radius:99px;overflow:hidden;background:var(--surface2);vertical-align:middle}
.b-conf{background:var(--accent)}.b-caut{background:var(--mix)}
.filters{position:sticky;top:calc(env(safe-area-inset-top,0px) + 148px);z-index:4;display:flex;flex-wrap:wrap;gap:6px;align-items:center;background:var(--bg);padding-block:8px;border-bottom:1px solid var(--line)}
.filters .lbl{font-size:.75rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-right:4px}
.fchip{appearance:none;font:inherit;font-size:.8rem;border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:99px;padding:3px 12px;cursor:pointer}
.fchip[aria-pressed="false"]{color:var(--muted);text-decoration:line-through;background:transparent}
details.node{background:var(--surface);border:1px solid var(--line);border-radius:6px;margin:8px 0;box-shadow:var(--shadow)}
details.depth1{margin-left:16px}details.depth2{margin-left:32px}
details.node>*:not(summary){margin-inline:14px}details.node>.scroll{margin-top:.2rem}
summary{cursor:pointer;padding:10px 14px;display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;list-style:none;font-weight:600}
summary::-webkit-details-marker{display:none}summary:before{content:"▸";color:var(--muted);font-size:.8rem}details[open]>summary:before{content:"▾"}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}.chip{font-size:.78rem;background:var(--surface2);border-radius:4px;padding:1px 8px;font-weight:500}
ul.quotes{list-style:none;padding:0;margin:.4rem 0 .9rem;display:grid;gap:10px}
li.q{display:grid;grid-template-columns:1.4rem 1fr;gap:6px}li.q[hidden]{display:none}
.dir{font-size:.85rem;text-align:center;padding-top:2px}.d-pos{color:var(--pos)}.d-neg{color:var(--neg)}.d-mix{color:var(--mix)}.d-neu{color:var(--neu)}
blockquote{margin:2px 0;padding:0;font:1rem/1.5 "Source Serif 4",Georgia,"Times New Roman",serif;max-width:72ch}
.meta{font-size:.76rem;color:var(--muted)}.tags{display:flex;gap:5px;flex-wrap:wrap}
.tag{font-size:.66rem;text-transform:uppercase;letter-spacing:.07em;font-weight:600;color:var(--muted)}
.tag+.tag:before{content:"·";margin-right:5px;color:var(--line)}
.t-competition{color:var(--accent)}.t-product{color:var(--pos)}.t-pressure{color:var(--neg)}.t-outlook{color:var(--mix)}.t-rev,.t-hedge,.t-cautious{color:var(--mix)}.t-confident{color:var(--accent)}
.hide-ids .src{display:none}.cov li{margin:.3rem 0}.cov{padding-left:1.1rem}
@media (max-width:640px){details.depth1{margin-left:6px}details.depth2{margin-left:12px}.chips{margin-left:0}}
@media (prefers-reduced-motion:no-preference){.tab{transition:color .15s}}
"""

JS = """
(function(){
  var root=document.querySelector('.wrap'),tabs=[].slice.call(document.querySelectorAll('.tab')),
      calls=[].slice.call(document.querySelectorAll('section.call'));
  function show(k){tabs.forEach(function(t){t.setAttribute('aria-selected',t.dataset.call===k?'true':'false')});
    calls.forEach(function(c){c.hidden=c.dataset.call!==k});try{localStorage.setItem('snap-tab',k)}catch(e){}}
  var start=tabs[0]&&tabs[0].dataset.call;try{var s=localStorage.getItem('snap-tab');if(s&&document.getElementById('call-'+s))start=s}catch(e){}
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.call)})});if(start)show(start);
  document.querySelectorAll('.fchip[data-bucket]').forEach(function(b){b.addEventListener('click',function(){
    var on=b.getAttribute('aria-pressed')!=='true';b.setAttribute('aria-pressed',on?'true':'false');
    document.querySelectorAll('li.q[data-bucket="'+b.dataset.bucket+'"]').forEach(function(q){q.hidden=!on})})});
  var ids=document.getElementById('toggle-ids');if(ids)ids.addEventListener('click',function(){
    var hidden=root.classList.toggle('hide-ids');ids.setAttribute('aria-pressed',hidden?'false':'true')});
})();
"""


def _find_snapshots() -> dict[str, Path]:
    """{stem: snapshot path}, across output/*_snapshot.json (py main.py) and every output/batch/<season>/calls/
    (py batch.py). The same call can exist in both (or in more than one season's batch run); the newest file wins,
    so a rescore after an extractor change is reflected without needing to touch this script."""
    found: dict[str, Path] = {}
    for snap_path in list(OUT.glob("*_snapshot.json")) + list(OUT.glob("batch/*/calls/*_snapshot.json")):
        stem = snap_path.name[: -len("_snapshot.json")]
        if stem not in found or snap_path.stat().st_mtime > found[stem].stat().st_mtime:
            found[stem] = snap_path
    return found


def main() -> int:
    calls = []
    for stem, snap_path in sorted(_find_snapshots().items()):
        facts_path = snap_path.with_name(f"{stem}_facts.json")
        if not facts_path.exists():
            continue
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        facts_result = json.loads(facts_path.read_text(encoding="utf-8"))
        gold = (ROOT / "gold" / "snapshot" / f"{stem}.json").exists()
        calls.append((stem, snap, facts_result, gold))
    if not calls:
        print("No snapshots found in output/ or output/batch/*/calls/. Run py main.py or py batch.py first.")
        return 1
    calls.sort(key=lambda c: (c[3], c[0]))            # calls nothing was tuned on first
    tabs = "".join(f'<button class="tab" role="tab" data-call="{esc(k)}" aria-selected="false">{esc(call_title(s["meta"]))}'
                   f'<small>{"validated on gold set" if g else "not yet validated"}</small></button>' for k, s, _, g in calls)
    filters = ('<div class="filters"><span class="lbl">Show</span>'
               + "".join(f'<button class="fchip" data-bucket="{b}" aria-pressed="true">{esc(_BUCKET_LABEL[b])}</button>'
                         for b in _BUCKET_ORDER if b != "leadership")
               + '<button class="fchip" id="toggle-ids" aria-pressed="true">Source ids</button></div>')
    body = "\n".join(call_section(k, s, f, g) for k, s, f, g in calls)
    page = (f'<title>Earnings Call Snapshots</title>\n'
            f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400&display=swap">\n'
            f'<style>{CSS}</style>\n<div class="top"><div class="wrap"><div class="brand">Earnings call snapshots</div>'
            f'<div class="tabs" role="tablist">{tabs}</div></div></div>\n<div class="wrap">{filters}\n{body}</div>\n<script>{JS}</script>\n')
    out = OUT / "snapshots.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page) / 1024:.0f} KB, {len(calls)} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

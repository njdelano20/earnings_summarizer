"""
charts.py
Chart rendering for the quarterly summary template (docs/summary_template.md): the four
chart-bearing sections -- headline scoreboard, business segments, capital allocation, balance
sheet snapshot. (Margin trend was cut as its own chart 2026-09-22 -- too thin on 1-2 real
quarterly points to be worth a dedicated section; a margin move is explained in prose wherever
it's actually driven by something, e.g. a segment mix shift.) Pure rendering layer: every
function takes already-curated numbers, not raw facts.json -- matching a transcript sentence to
the right segment/metric is a separate, not-yet-built problem (see the template doc's "Open
engineering work"); these functions assume that matching has already happened, by hand or
otherwise.

Palette, color jobs (categorical/status), and mark conventions follow the dataviz skill's
validated default (OKLCH-checked for colorblind separation -- see its references/palette.md);
hexes are copied below rather than imported since this project has no dependency on that skill's
package. Static PNGs only, matching this project's existing convention (Markdown reports +
embedded images, not interactive HTML) -- the skill's hover/dark-mode sections don't apply here.
Pixel-perfect rounded bar caps and the 2px surface-gap spec are not implemented (pure polish, not
worth the added complexity for a local reporting tool); the substantive checks (correct color
JOB per chart -- categorical for distinct-category bars, status for good/bad, never both mixed;
direct value labels so nothing relies on color alone; recessive muted gridlines; a legend when
there's more than one category) are all honored. The Markdown table already placed beside each
chart in the sample doc is this skill's required "table view" accessible alternative -- charts
here never replace the table, only sit next to it.

    py charts.py --demo    regenerates every AAPL Q3 2026 sample chart in docs/ from the numbers
                            already written up in docs/sample_AAPL_Q3_2026.md, as a visual smoke
                            test after any change to this module
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"

# -- validated palette (dataviz skill references/palette.md, light mode) ----------------------- #
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"

# categorical: fixed hue order, slots 1-4 (blue, orange, aqua, yellow) -- for genuinely distinct
# nominal categories (e.g. dividends vs. buybacks vs. capex), never for a good/bad reading.
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
# status: reserved, fixed meaning, always paired with a visible value/direction label -- never
# color alone. Used only where the encoding IS good/bad (yoy growth direction, net cash vs. debt).
STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"
STATUS_MISSING = "#d8d6cd"    # "not stated" bars -- never fabricated, always labeled as such


def _new_fig(figsize=(8, 5)):
    fig, ax = plt.subplots(figsize=figsize, dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    return fig, ax


def _style_ax(ax, y_grid=True):
    """Recessive chrome: hairline gridlines, muted ticks, no top/right/left spines."""
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, labelsize=10, length=0)
    if y_grid:
        ax.yaxis.grid(True, color=GRIDLINE, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.set_yticklabels([])   # bars are directly labeled; ticks would just repeat the value


def _title(ax, title: str):
    ax.set_title(title, fontsize=13, color=INK_PRIMARY, loc="left", pad=14, fontweight="bold")


def _save(fig, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    # bbox_inches="tight" (not just fig.tight_layout()) so legends/titles placed outside the axes
    # box -- e.g. the capital-allocation pie's legend via bbox_to_anchor -- never clip at the edge
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------- #
# 1. headline scoreboard -- a stat-tile row, not a bar chart (metrics don't share a unit)
# --------------------------------------------------------------------------- #

def headline_scoreboard_chart(metrics: list[dict], title: str, out_path: Path) -> Path:
    """metrics: [{"label": "Revenue", "value": "$109.40B", "delta": "+16% yoy", "good": True,
    "beat": "Beat by 0.4%", "beat_good": True, "guidance": "Sept guide: 9-11% (Street: +10.9%)"},
    ...]. `delta`/`good` (yoy or qoq change), `beat`/`beat_good` (vs. analyst consensus), and
    `guidance` (forward-looking line for next period) are all optional and independent -- a
    metric with nothing real to show for one of them just leaves that line out entirely, never
    a placeholder like "n/a" or "not estimated". `guidance` prefers the company's own stated
    guide when one exists (e.g. AAPL guides revenue growth and gross margin directly); when it
    doesn't (AAPL never guides EPS itself), fall back to the analyst consensus estimate for that
    period instead, labeled "Street ..." so it's never mistaken for a company guide. A wide,
    short banner -- meant to span the full page width, not sit in a half-width column."""
    n = len(metrics)
    fig, axes = plt.subplots(1, n, figsize=(3.1 * n, 2.7), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    if n == 1:
        axes = [axes]
    for i, (ax, m) in enumerate(zip(axes, metrics)):
        ax.set_facecolor(SURFACE)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        if i > 0:
            ax.axvline(-0.06, ymin=0.04, ymax=0.96, color=GRIDLINE, linewidth=1, clip_on=False)
        ax.text(0.5, 0.89, m["label"], ha="center", va="center", fontsize=12, color=INK_SECONDARY)
        ax.text(0.5, 0.66, m["value"], ha="center", va="center", fontsize=22,
                color=INK_PRIMARY, fontweight="bold")
        delta = m.get("delta")
        if delta:
            good = m.get("good")
            color = STATUS_GOOD if good else STATUS_CRITICAL if good is False else INK_MUTED
            ax.text(0.5, 0.45, delta, ha="center", va="center", fontsize=11.5,
                    color=color, fontweight="bold")
        beat = m.get("beat")
        if beat:
            beat_good = m.get("beat_good")
            color = STATUS_GOOD if beat_good else STATUS_CRITICAL if beat_good is False else INK_MUTED
            ax.text(0.5, 0.27, beat, ha="center", va="center", fontsize=10.5, color=color)
        guidance = m.get("guidance")
        if guidance:
            ax.text(0.5, 0.06, guidance, ha="center", va="center", fontsize=9.5,
                    color=INK_MUTED, style="italic")
    fig.suptitle(title, fontsize=14, color=INK_PRIMARY, x=0.015, ha="left", fontweight="bold", y=1.02)
    fig.subplots_adjust(wspace=0.05)
    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# 2. business segments -- status color (growth direction), never fabricated for missing segments
# --------------------------------------------------------------------------- #

def segment_revenue_chart(segments: list[dict], title: str, out_path: Path) -> Path:
    """segments: [{"name": "iPhone", "revenue_b": 54.3, "yoy_pct": 22}, ...]"""
    fig, ax = _new_fig(figsize=(8.5, 7.5))
    names = [s["name"] for s in segments]
    values = [s["revenue_b"] for s in segments]
    colors = [STATUS_GOOD if s["yoy_pct"] >= 0 else STATUS_CRITICAL for s in segments]
    bars = ax.bar(names, values, color=colors, width=0.62, zorder=3)
    for bar, s in zip(bars, segments):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.015,
                f"${s['revenue_b']:.1f}B\n({s['yoy_pct']:+.0f}% yoy)",
                ha="center", va="bottom", fontsize=9.5, color=INK_PRIMARY)
    ax.set_ylim(0, max(values) * 1.22)
    ax.set_ylabel("Revenue ($B)", fontsize=10, color=INK_SECONDARY)
    _style_ax(ax)
    ax.set_yticks(ax.get_yticks())
    ax.set_yticklabels([f"{int(t)}" for t in ax.get_yticks()])   # keep axis scale readable
    ax.tick_params(axis="x", labelsize=10, colors=INK_PRIMARY)
    _title(ax, title)
    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# 3. capital allocation -- true nominal categories, fixed categorical order, pie + legend.
# M&A is deliberately never a slice here (see summary_template.md) -- it belongs in the capital
# allocation TEXT as a callout when the call discusses one, not charted as a cash outflow unless
# SEC has actually filed a real acquisitions cash-flow figure for the period.
# --------------------------------------------------------------------------- #

def capital_allocation_chart(uses: dict[str, float], title: str, out_path: Path) -> Path:
    """uses: {"Dividends paid": 4.0, "Share repurchases": 25.8, "CapEx": 2.455, "Debt repaid": 0.232} --
    every value here must be real (SEC-filed or transcript-stated); a use with no real figure for
    this period is simply left out of the dict, never included as a zero or a guess."""
    fig, ax = _new_fig(figsize=(8, 5.4))
    ax.axis("off")
    names = list(uses.keys())
    values = list(uses.values())
    colors = [CATEGORICAL[i % len(CATEGORICAL)] for i in range(len(names))]
    total = sum(values) or 1.0

    def _pct_label(pct):
        return f"${pct / 100 * total:.1f}B\n({pct:.0f}%)" if pct >= 4 else ""   # skip labels too small to fit

    wedges, _, autotexts = ax.pie(
        values, colors=colors, autopct=_pct_label, pctdistance=0.72, startangle=90,
        counterclock=False, wedgeprops={"linewidth": 2, "edgecolor": SURFACE},
        textprops={"fontsize": 9.5, "color": INK_PRIMARY, "ha": "center"})
    legend_labels = [f"{n}  (${v:.1f}B)" for n, v in zip(names, values)]
    ax.legend(wedges, legend_labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
             frameon=False, fontsize=10, labelcolor=INK_PRIMARY)
    ax.set_title(title, fontsize=13, color=INK_PRIMARY, loc="left", pad=14, fontweight="bold",
                x=-0.05)
    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# 4. balance sheet snapshot -- cash/debt are neutral categories; net position is status-colored
# --------------------------------------------------------------------------- #

def balance_sheet_chart(cash_b: float, debt_b: float, net_b: float, title: str, out_path: Path,
                        maturity_b: dict[str, float] | None = None,
                        maturity_as_of: str | None = None) -> Path:
    """maturity_b: {"Next 12mo": 12.4, "Year 2": 10.1, ...} -- real filed principal-repayment
    amounts by year bucket (SEC's own debt-maturity-ladder tags), optional. When given, renders as
    a second panel next to the cash/debt/net bars rather than a separate image -- these two are
    read as one "balance sheet" picture, and the two-column layout only pulls one image per
    section."""
    # stacked vertically, not side by side -- this image sits in a narrow sidebar column next to
    # the section text, where a wide 2-panel figure would scale down to an awkwardly short strip
    n_panels = 2 if maturity_b else 1
    fig, axes = plt.subplots(n_panels, 1, figsize=(6.5, 5.6 if maturity_b else 4.4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax = axes[0] if maturity_b else axes
    ax.set_facecolor(SURFACE)

    bars_spec = [("Cash & securities", cash_b, CATEGORICAL[0]),
                ("Total debt", debt_b, CATEGORICAL[1]),
                ("Net cash" if net_b >= 0 else "Net debt", net_b,
                 STATUS_GOOD if net_b >= 0 else STATUS_CRITICAL)]
    top = max(abs(v) for _, v, _ in bars_spec) or 1.0
    for i, (name, val, color) in enumerate(bars_spec):
        ax.bar(i, val, color=color, width=0.55, zorder=3)
        ax.text(i, val + (top * 0.03 if val >= 0 else -top * 0.06),
                f"${val:.0f}B", ha="center", va="bottom" if val >= 0 else "top",
                fontsize=10.5, color=INK_PRIMARY, fontweight="bold")
    ax.set_xticks(range(len(bars_spec)))
    ax.set_xticklabels([b[0] for b in bars_spec], fontsize=10, color=INK_PRIMARY)
    ax.axhline(0, color=BASELINE, linewidth=1)
    ax.set_ylim(-top * 0.2, top * 1.25)
    ax.set_ylabel("$B", fontsize=10, color=INK_SECONDARY)
    _style_ax(ax)
    ax.set_yticks(ax.get_yticks())
    ax.set_yticklabels([f"{int(t)}" for t in ax.get_yticks()])
    _title(ax, title)

    if maturity_b:
        ax2 = axes[1]
        ax2.set_facecolor(SURFACE)
        names = list(maturity_b.keys())
        values = list(maturity_b.values())
        mtop = max(values) or 1.0
        ax2.bar(range(len(names)), values, color=CATEGORICAL[0], width=0.6, zorder=3)
        for i, v in enumerate(values):
            ax2.text(i, v + mtop * 0.02, f"${v:.1f}B", ha="center", va="bottom",
                     fontsize=9, color=INK_PRIMARY)
        ax2.set_xticks(range(len(names)))
        ax2.set_xticklabels(names, fontsize=9, color=INK_PRIMARY)
        ax2.set_ylim(0, mtop * 1.25)
        ax2.set_ylabel("$B", fontsize=10, color=INK_SECONDARY)
        _style_ax(ax2)
        ax2.set_yticks(ax2.get_yticks())
        ax2.set_yticklabels([f"{int(t)}" for t in ax2.get_yticks()])
        subtitle = "Debt principal due by year"
        if maturity_as_of:
            subtitle += f" (as of {maturity_as_of})"
        _title(ax2, subtitle)

    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# demo / smoke test -- the real AAPL Q3 2026 numbers from docs/sample_AAPL_Q3_2026.md
# --------------------------------------------------------------------------- #

def _demo() -> None:
    headline_scoreboard_chart(
        [{"label": "Revenue", "value": "$109.4B", "delta": "+16% yoy", "good": True,
          "beat": "Beat consensus by +0.4%", "beat_good": True,
          "guidance": "Sept guide: +9-11% yoy (Street: +10.9%)"},
         {"label": "EPS", "value": "$2.02", "delta": "+29% yoy", "good": True,
          "beat": "Beat consensus by +6.7%", "beat_good": True,
          "guidance": "Street Sept EPS: $1.98 (no company guide)"},
         {"label": "Gross margin", "value": "50.1%", "delta": "+80 bps qoq", "good": True,
          "guidance": "Sept guide: 47-48%"},
         {"label": "Operating cash flow", "value": "$34.4B", "delta": None, "good": None}],
        "AAPL Q3 2026 (June quarter) -- Headline scoreboard",
        DOCS / "sample_AAPL_Q3_2026_scoreboard.png")

    segment_revenue_chart(
        [{"name": "iPhone", "revenue_b": 54.3, "yoy_pct": 22},
         {"name": "Mac", "revenue_b": 10.4, "yoy_pct": 29},
         {"name": "iPad", "revenue_b": 6.2, "yoy_pct": -6},
         {"name": "Wearables/Home/Acc.", "revenue_b": 7.9, "yoy_pct": 6},
         {"name": "Services", "revenue_b": 30.7, "yoy_pct": 12}],
        "AAPL Q3 2026 (June quarter) -- Revenue by segment",
        DOCS / "sample_AAPL_Q3_2026_segments.png")

    capital_allocation_chart(
        {"Dividends paid": 4.0, "Share repurchases": 25.8, "CapEx": 2.455, "Debt repaid": 0.232},
        "AAPL Q3 2026 (June quarter) -- Capital allocation",
        DOCS / "sample_AAPL_Q3_2026_capital.png")

    balance_sheet_chart(
        147, 84, 63,
        "AAPL Q3 2026 (June quarter) -- Balance sheet snapshot",
        DOCS / "sample_AAPL_Q3_2026_balance.png",
        maturity_b={"Next 12mo": 12.4, "Year 2": 10.1, "Year 3": 9.3,
                   "Year 4": 5.2, "Year 5": 5.0, "After 5yr": 49.3},
        maturity_as_of="FY2025 10-K, Sep 2025")

    print(f"Wrote 4 charts to {DOCS}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="regenerate the AAPL Q3 2026 sample charts")
    args = ap.parse_args()
    if args.demo:
        _demo()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

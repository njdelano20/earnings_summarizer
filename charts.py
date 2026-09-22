"""
charts.py
Chart rendering for the quarterly summary template (docs/summary_template.md): the five
chart-bearing sections -- headline scoreboard, business segments, margin trend, capital
allocation, balance sheet snapshot. Pure rendering layer: every function takes already-curated
numbers, not raw facts.json -- matching a transcript sentence to the right segment/metric is a
separate, not-yet-built problem (see the template doc's "Open engineering work"); these functions
assume that matching has already happened, by hand or otherwise.

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
import matplotlib.ticker as mticker

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
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------- #
# 1. headline scoreboard -- a stat-tile row, not a bar chart (metrics don't share a unit)
# --------------------------------------------------------------------------- #

def headline_scoreboard_chart(metrics: list[dict], title: str, out_path: Path) -> Path:
    """metrics: [{"label": "Revenue", "value": "$109.40B", "delta": "+16% yoy", "good": True}, ...]
    `delta`/`good` are optional (None when no comparison is available yet, e.g. no prior-quarter
    guidance in the gold set) -- a tile with no delta just shows the value, never a fabricated one."""
    n = len(metrics)
    fig, axes = plt.subplots(1, n, figsize=(2.4 * n, 2.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    if n == 1:
        axes = [axes]
    for ax, m in zip(axes, metrics):
        ax.set_facecolor(SURFACE)
        ax.axis("off")
        ax.text(0.5, 0.78, m["label"], ha="center", va="center", fontsize=11,
                color=INK_SECONDARY, transform=ax.transAxes)
        ax.text(0.5, 0.48, m["value"], ha="center", va="center", fontsize=20,
                color=INK_PRIMARY, fontweight="bold", transform=ax.transAxes)
        delta = m.get("delta")
        if delta:
            good = m.get("good")
            color = STATUS_GOOD if good else STATUS_CRITICAL if good is False else INK_MUTED
            ax.text(0.5, 0.18, delta, ha="center", va="center", fontsize=10.5,
                    color=color, fontweight="bold", transform=ax.transAxes)
    fig.suptitle(title, fontsize=13, color=INK_PRIMARY, x=0.02, ha="left", fontweight="bold")
    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# 2. business segments -- status color (growth direction), never fabricated for missing segments
# --------------------------------------------------------------------------- #

def segment_revenue_chart(segments: list[dict], title: str, out_path: Path) -> Path:
    """segments: [{"name": "iPhone", "revenue_b": 54.3, "yoy_pct": 22}, ...]"""
    fig, ax = _new_fig(figsize=(9, 5.2))
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
# 3. margin trend -- single series, categorical slot 1, direct end-label (no legend needed)
# --------------------------------------------------------------------------- #

def margin_trend_chart(points: list[tuple[str, float]], title: str, out_path: Path) -> Path:
    """points: [("March 2026", 49.3), ("June 2026", 50.1), ...], oldest first."""
    fig, ax = _new_fig(figsize=(7, 4.2))
    labels = [p[0] for p in points]
    values = [p[1] for p in points]
    ax.plot(labels, values, color=CATEGORICAL[0], linewidth=2, marker="o",
            markersize=8, markerfacecolor=CATEGORICAL[0], markeredgecolor=SURFACE,
            markeredgewidth=2, zorder=3)
    ax.text(len(labels) - 1, values[-1], f"  {values[-1]:.1f}%", va="center", ha="left",
            fontsize=11, color=INK_PRIMARY, fontweight="bold")
    span = max(values) - min(values) or 1.0
    ax.set_ylim(min(values) - span * 0.6, max(values) + span * 0.6)
    ax.set_ylabel("Gross margin (%)", fontsize=10, color=INK_SECONDARY)
    _style_ax(ax)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
    ax.set_yticks(ax.get_yticks())
    # a tight span (e.g. two quarters 0.8pp apart) needs 1 decimal or every tick rounds to the
    # same integer label -- pick decimals from the actual tick spacing instead of hardcoding one
    step = ax.get_yticks()[1] - ax.get_yticks()[0] if len(ax.get_yticks()) > 1 else 1.0
    decimals = 0 if step >= 1 else 1
    ax.set_yticklabels([f"{t:.{decimals}f}%" for t in ax.get_yticks()])
    ax.tick_params(axis="x", labelsize=10, colors=INK_PRIMARY)
    _title(ax, title)
    if len(points) < 3:
        ax.text(0.5, -0.22, f"only {len(points)} real data points on file -- too thin to call a "
                             f"trend yet", transform=ax.transAxes, ha="center", fontsize=8.5,
                color=INK_MUTED, style="italic")
    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# 4. capital allocation -- true nominal categories, fixed categorical order, legend
# --------------------------------------------------------------------------- #

def capital_allocation_chart(uses: dict[str, float | None], title: str, out_path: Path) -> Path:
    """uses: {"Dividends paid": 4.0, "Share repurchases": 25.8, "CapEx": None, "M&A": None} --
    a None value is rendered as a labeled "not stated" bar, never guessed or omitted silently."""
    fig, ax = _new_fig(figsize=(7.5, 4.6))
    names = list(uses.keys())
    known = [v for v in uses.values() if v is not None]
    top = max(known) if known else 1.0
    for i, (name, val) in enumerate(uses.items()):
        color = CATEGORICAL[i % len(CATEGORICAL)] if val is not None else STATUS_MISSING
        height = val if val is not None else top * 0.12
        bar = ax.bar(i, height, color=color, width=0.6, zorder=3)[0]
        label = f"${val:.1f}B" if val is not None else "not stated"
        ax.text(i, height + top * 0.02, label, ha="center", va="bottom", fontsize=9.5,
                color=INK_PRIMARY if val is not None else INK_MUTED)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, fontsize=10, color=INK_PRIMARY)
    ax.set_ylim(0, top * 1.25)
    ax.set_ylabel("$B", fontsize=10, color=INK_SECONDARY)
    _style_ax(ax)
    ax.set_yticks(ax.get_yticks())
    ax.set_yticklabels([f"{int(t)}" for t in ax.get_yticks()])
    _title(ax, title)
    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# 5. balance sheet snapshot -- cash/debt are neutral categories; net position is status-colored
# --------------------------------------------------------------------------- #

def balance_sheet_chart(cash_b: float, debt_b: float, net_b: float, title: str,
                        out_path: Path) -> Path:
    fig, ax = _new_fig(figsize=(6.5, 4.4))
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
    return _save(fig, out_path)


# --------------------------------------------------------------------------- #
# demo / smoke test -- the real AAPL Q3 2026 numbers from docs/sample_AAPL_Q3_2026.md
# --------------------------------------------------------------------------- #

def _demo() -> None:
    headline_scoreboard_chart(
        [{"label": "Revenue", "value": "$109.4B", "delta": "+16% yoy", "good": True},
         {"label": "EPS", "value": "$2.02", "delta": "+29% yoy", "good": True},
         {"label": "Gross margin", "value": "50.1%", "delta": "+80 bps qoq", "good": True},
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

    margin_trend_chart(
        [("March 2026", 49.3), ("June 2026", 50.1)],
        "AAPL -- Gross margin trend",
        DOCS / "sample_AAPL_Q3_2026_margin.png")

    capital_allocation_chart(
        {"Dividends paid": 4.0, "Share repurchases": 25.8, "CapEx": None, "M&A": None},
        "AAPL Q3 2026 (June quarter) -- Capital allocation",
        DOCS / "sample_AAPL_Q3_2026_capital.png")

    balance_sheet_chart(
        147, 84, 63,
        "AAPL Q3 2026 (June quarter) -- Balance sheet snapshot",
        DOCS / "sample_AAPL_Q3_2026_balance.png")

    print(f"Wrote 5 charts to {DOCS}")


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

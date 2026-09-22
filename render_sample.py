"""
render_sample.py
Renders a quick-summary Markdown doc (docs/sample_*.md, following summary_template.md) into a
two-column HTML preview: any section with a chart image gets the image on one side and its
text/table on the other, side by side, instead of everything stacking vertically. A section
with no image (guidance, competitive environment, product development, macro/regulatory --
none of these are chart-bearing per the template spec) renders full-width instead of being
forced into a fake second column.

Layout detection is generic, not hardcoded per section: any Markdown `##` section whose first
child is an `<img>` becomes two-column automatically; anything else stays full-width. This
means the .md stays the single source of truth for content -- this script never duplicates
prose, it only re-lays-out whatever markdown.markdown() already produced.

    py -m pip install markdown beautifulsoup4
    py render_sample.py docs/sample_AAPL_Q3_2026.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import markdown
from bs4 import BeautifulSoup, NavigableString

CSS = """
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 1200px;
       margin: 40px auto; padding: 0 24px; color: #0b0b0b; background: #fcfcfb; line-height: 1.55; }
h1 { font-size: 1.8rem; border-bottom: 2px solid #0b0b0b; padding-bottom: 10px; }
h2 { font-size: 1.3rem; margin-top: 2.6em; border-bottom: 1px solid #e1e0d9; padding-bottom: 6px; }
h3 { font-size: 1.05rem; }
img { max-width: 100%; display: block; border: 1px solid #e1e0d9; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.9rem; }
th, td { border: 1px solid #e1e0d9; padding: 6px 10px; text-align: left; }
th { background: #f2f1ec; }
code { background: #f2f1ec; padding: 1px 5px; border-radius: 3px; font-size: 0.88em; }
em { color: #52514e; }
hr { border: none; border-top: 1px solid #e1e0d9; margin: 2.5em 0; }
.section-row { display: flex; gap: 32px; align-items: flex-start; margin-top: 12px; }
.section-row > .chart-col { flex: 0 0 52%; }
.section-col { flex: 1 1 auto; min-width: 0; }
.section-full { margin-top: 12px; }
.section-full.banner img { width: 100%; }
@media (max-width: 860px) { .section-row { flex-direction: column; } .section-row > .chart-col { flex-basis: auto; } }
"""


def render(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    body_html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    soup = BeautifulSoup(body_html, "html.parser")

    out = BeautifulSoup("", "html.parser")
    intro: list = []
    sections: list[tuple] = []   # (h2_tag, [content_tags])
    current = None
    for el in list(soup.children):
        if isinstance(el, NavigableString) and not el.strip():
            continue
        if getattr(el, "name", None) == "h2":
            current = (el, [])
            sections.append(current)
        elif current is None:
            intro.append(el)
        else:
            current[1].append(el)

    for el in intro:
        out.append(el)

    for h2, content in sections:
        out.append(h2)
        img = None
        rest = []
        for el in content:
            if img is None and getattr(el, "name", None) == "img":
                img = el
                continue
            # an <img> nested one level down (e.g. inside a <p>) also counts
            if img is None and getattr(el, "name", None) == "p" and el.find("img"):
                img = el.find("img")
                el.find("img").extract()
                if el.get_text(strip=True):
                    rest.append(el)
                continue
            rest.append(el)

        has_text = any(el.get_text(strip=True) for el in rest) if rest else False
        if img is not None and has_text:
            row = out.new_tag("div", **{"class": "section-row"})
            chart_col = out.new_tag("div", **{"class": "chart-col"})
            chart_col.append(img)
            text_col = out.new_tag("div", **{"class": "section-col"})
            for el in rest:
                text_col.append(el)
            row.append(chart_col)
            row.append(text_col)
            out.append(row)
        elif img is not None:
            # no other content in this section -- a full-width banner, not a two-column row
            # with an empty second half (e.g. the headline scoreboard, chart-only by design)
            wrap = out.new_tag("div", **{"class": "section-full banner"})
            wrap.append(img)
            out.append(wrap)
        else:
            wrap = out.new_tag("div", **{"class": "section-full"})
            for el in rest:
                wrap.append(el)
            out.append(wrap)

    title = "Quick Summary"
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
{out}
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md_path", help="the sample Markdown doc to render")
    ap.add_argument("--out", help="output .html path (default: same name, .html extension)")
    args = ap.parse_args()

    md_path = Path(args.md_path)
    out_path = Path(args.out) if args.out else md_path.with_suffix(".html")
    out_path.write_text(render(md_path), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

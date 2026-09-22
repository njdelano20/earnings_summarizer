# Quarterly summary template

Design spec for a standardized, mostly-visual "quick summary" report generated per call,
worked out 2026-09-22 and reworked the same day after a hands-on design review against the
first real build (see `sample_AAPL_Q3_2026.md`). Distinct from the existing detailed
`_snapshot.md` output (the full per-segment fact ledger + trajectory table + coverage
report) -- this is the shorter, template-driven readout meant to sit alongside it, built from
a fixed section list rather than however much commentary a given call happened to produce.

## Design principles

- **Numbers come from a data source, never from the transcript, wherever a source exists.**
  The transcript supplies commentary/explanation (the "why"), not the figure itself. This
  keeps the numeric sections as trustworthy as MarketDataLibrary's/SEC EDGAR's own data, and
  keeps the transcript extractor doing what it's already good at (topic/stance signals on
  real sentences) instead of generating numbers, which is the riskier failure mode.
- **A section earns its place by having something real to say.** Margin trend was cut as a
  standalone section (see below) specifically because 1-2 real quarterly points isn't enough
  to call anything a trend -- better to fold a real, explained margin move into the section
  that actually explains it (Business segments) than to ship a thin, apologetic chart.
- **Graphics wherever the underlying data is chartable and genuinely benefits from one.**
  Text is reserved for sections with no numeric anchor, or where a chart would encode less
  than the number itself (e.g. a single "$0 debt issued" fact needs a sentence, not a chart).
- **No aggregate/rollup tone score.** Per-topic stance (already produced by `signals.py`,
  evidence-linked to a real sentence) stays attached to its segment's commentary. Collapsing
  it into one call-wide number was considered and rejected: it throws away exactly the
  nuance ("confident on segment A, cautious on segment B") that makes per-topic stance
  useful, and management tone is coached to sound positive regardless of substance, so an
  aggregate read risks reading as confident-sounding output with weak real signal.
- **"Products" is not the same thing as "business segments."** A reported segment
  (`segments.item`, e.g. Apple's "iPhone") is a *financial reporting aggregation* that can
  cover many actual products underneath it, not a product list. Named products, launches,
  and roadmap content belong in Product development, not Business segments -- the two
  sections can (and often will) discuss the same underlying business area from different
  angles: segments gives the real revenue number, product development gives what's shipping.
  Product development is itself one bullet PER PRODUCT (name, quick explanation, cross-
  reference to the segment/competitive angle it feeds), never one undifferentiated paragraph.
- **Analysis, not reported speech.** Text sections read as "here's what's happening, here's
  the plausible read / potential outcome" -- never "management said X." A short direct quote
  is fine as evidence for a claim (matches this project's evidence-linking philosophy
  elsewhere), but the section's own voice is analytical, not a transcript of what was said.
  This applies most in Competitive environment and Product development, but holds everywhere.
- **A pie/bar/line chart only when the underlying quantity should visually sum to something
  or compare cleanly on one axis.** Capital allocation is a pie (its slices are literal uses
  of one pool of cash, so a whole-to-parts chart is the right form); the headline scoreboard
  is a row of stat tiles, not a bar chart, because its metrics don't share a unit (dollars,
  EPS, percent side by side would encode nothing real in bar height).
- **Never chart a use of cash that isn't a real, filed, completed cash outflow for the
  period.** M&A is deliberately excluded from the Capital allocation chart even when a deal
  is *discussed* on the call -- a pie slice implies money that has actually moved. A
  discussed-but-not-yet-executed deal (a supply agreement, an LOI, a pending close) belongs
  in that section's TEXT as a forward-looking callout, cross-checked against SEC's own
  acquisitions cash-flow tag where possible (a real $0 there is itself informative -- it
  confirms the deal hasn't hit the cash-flow statement as a completed purchase).

## Section list

| # | Section | Source | Visual | Notes |
|---|---|---|---|---|
| 1 | Headline scoreboard | transcript facts (revenue/EPS actual) + prior-quarter guidance | stat-tile row (yoy delta today; actual-vs-guided delta once a prior call exists) | Actual vs. what was guided last call, not vs. analyst consensus (no consensus data in this pipeline). No standalone prose -- the tiles and the table are the whole section. |
| 2 | Business segments | `segments` (MarketDataLibrary) when available; transcript facts as fallback | bar chart: revenue by segment, colored by yoy growth direction (status green/red, never plain categorical) | Coverage varies a lot by company -- **checked directly, not assumed**: AAPL has NO `Revenue by Segment` rows in `segments` at all, so for AAPL this section runs entirely on transcript-extracted facts. Don't assume the DB fills this gap even for large, well-covered tickers -- check per symbol. Text is **one bullet per segment** naming its driver, not a flowing paragraph -- and this is also where any real, explained margin move for the quarter gets folded in (see "margin context" below), since a margin move is usually a segment-mix story anyway. |
| 3 | Capital allocation | transcript facts (dividends, buybacks) + SEC EDGAR 10-Q via `sec_edgar.py` (CapEx, debt issued/repaid) | pie chart, one slice per real cash use | SEC's cash-flow-statement XBRL tags are typically YTD-cumulative only, not a discrete quarter (unlike revenue/EPS) -- `sec_edgar.find_quarterly_value()` derives the standalone quarter as (this period's YTD) minus (the prior quarter's YTD), both real filed numbers, flagged `derived_from_ytd: True` on the result so a report can disclose the method. M&A is never a chart slice (see design principles) -- mention a discussed-but-unexecuted deal in this section's text instead, cross-checked against SEC's acquisitions tag. |
| 4 | Balance sheet snapshot | `financials` balance_sheet (Total Debt, Net Cash/Debt, Shareholders' Equity) | simple debt/cash/equity bar, net position status-colored | **Blocked as of 2026-09-22**: ~5,042 of 5,050 symbols missing Liabilities/Equity data; the fix (`refetch_balance_sheet_liabilities.py` in MarketDataLibrary) exists and was running at the time this note was written -- check whether it's finished before assuming this gap is still open. Falls back to whatever cash/debt the call states directly until confirmed fixed. |
| 5 | Insider activity | Alpha Vantage `INSIDER_TRANSACTIONS` | table, no chart (a small number of named individuals reads better as a table than a chart) | New 2026-09-22. Loadable into MarketDataLibrary via `add_insider_transactions_data.py` (new script there) into a new `insider_transactions` table -- free-tier quota shared with the transcript fetch, so don't run it at full-universe scale without a paid key. Scope the reporting window to the call's own quarter. Separate real open-market transactions (a real `share_price`) from RSU-vesting mechanics (grant/withholding pairs at a $0 or blank price) before writing anything analytical -- conflating the two overstates "insider selling" with routine, scheduled equity-comp administration. Never claim to know *why* someone sold (plan-based vs. discretionary) unless the data actually says so. |
| 6 | Forward guidance | transcript facts (new guidance stated this call) | small table or delta vs. this quarter's actual | This is the "guided" half of section 1's next-quarter comparison; existing `trajectory` logic in `snapshot.py` already computes reported-vs-guided deltas -- reuse it rather than rebuilding. |
| 7 | Competitive environment | transcript only | text, analytical voice (see design principles) | No MarketDataLibrary source exists for this -- purely `signals.py` topic/stance extraction, same as today, but written up as "what's happening / what it plausibly means," not reported speech. |
| 8 | Product development | transcript only | text, **one bullet per named product** | No DB source -- distinct from section 2, see design principles. Each bullet: what it is, in one sentence why it matters, and a cross-reference to whatever segment/competitive claim it's actually driving, when there is one. |
| 9 | Macro/regulatory | transcript only | text, analytical voice | Kept separate from Competitive environment on purpose -- FX, tariffs, rates, and regulation are not "what a competitor did" and get conflated with competitive commentary if not their own bucket. |

**Cut from the section list (2026-09-22): standalone "Margin trend."** Originally section 3.
With only 1-2 real quarterly points on file for any given symbol so far, a dedicated trend
chart was mostly an apology for having too little data. A real, explained margin move belongs
in Business segments instead, where the driver (usually a segment-mix shift) actually lives.
Revisit as its own section again once either quarterly `financials` exist in MarketDataLibrary
or enough consecutive real calls accumulate per symbol in the gold set to make a genuine
multi-quarter chart -- at that point it's a real trend, not two dots and an apology.

**Bottom of every report: source documents.** A link to the full transcript (the cached raw
text file) and to the real SEC filing this quarter's cross-checked figures came from (built
via `sec_edgar.py`'s submissions API -- the real filed document URL, not a generic SEC search
link). Lets a reader verify anything in the summary against the primary source directly.

**Layout: side-by-side, not stacked.** Each section's chart and its text/table sit next to
each other (chart on one side, text on the other) rather than the chart stacked above the
text -- a presentation-layer concern, handled by the HTML renderer, not by anything in this
Markdown spec itself (Markdown alone can't express a real two-column layout reliably).

## Open engineering work (not yet started)

- Matching a transcript sentence to the right `segments.item`/product name by text (e.g.
  recognizing "iPhone revenue was strong" refers to the `iPhone` segment) is a real
  text-matching problem, not a data problem -- this is where the actual engineering effort
  belongs, not in defining a product list upfront. Deliberately deferred twice now (chart
  work, then this design rework, both took priority).
- A `segments` coverage check per symbol (does this company report at product/segment
  granularity at all?) should run before deciding whether section 2 can be DB-driven or needs
  to fall back to transcript-only, rather than assuming based on company size/prominence.
- Insider-activity classification (real open-market transaction vs. RSU-vesting mechanics) is
  currently done by hand per report; a reusable classifier belongs in code once this section
  has run against more than one symbol.

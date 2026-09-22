# Quarterly summary template

Design spec for a standardized, mostly-visual "quick summary" report generated per call,
worked out 2026-09-22. Distinct from the existing detailed `_snapshot.md` output (the full
per-segment fact ledger + trajectory table + coverage report) -- this is the shorter,
template-driven readout meant to sit alongside it, built from a fixed section list rather
than however much commentary a given call happened to produce.

## Design principles

- **Numbers come from a data source, never from the transcript, wherever a source exists.**
  The transcript supplies commentary/explanation (the "why"), not the figure itself. This
  keeps the numeric sections as trustworthy as MarketDataLibrary's own data, and keeps the
  transcript extractor doing what it's already good at (topic/stance signals on real
  sentences) instead of generating numbers, which is the riskier failure mode.
- **Graphics wherever the underlying data is chartable.** Text is reserved for sections with
  no numeric anchor. Five of the nine sections below are chart-bearing; four are text-only.
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

## Section list

| # | Section | Source | Visual | Notes |
|---|---|---|---|---|
| 1 | Headline scoreboard | transcript facts (revenue/EPS actual) + prior-quarter guidance | small bar/delta | Actual vs. what was guided last call, not vs. analyst consensus (no consensus data in this pipeline). |
| 2 | Business segments | `segments` (MarketDataLibrary) when available; transcript facts as fallback | bar or stacked bar: revenue by segment, this period vs. prior | Coverage varies a lot by company -- **checked directly, not assumed**: AAPL has NO `Revenue by Segment` rows in `segments` at all (only Revenue by Geography / EBIT by Geography / Gross Profit / Key Performance Indicators), so for AAPL this section runs entirely on transcript-extracted facts. Don't assume the DB fills this gap even for large, well-covered tickers -- check per symbol. |
| 3 | Margin trend | `financials` Additional Metrics (Gross/Operating/Profit/EBITDA margin), trailing periods | line chart | `financials` is annual (FY) + TTM today, not quarterly -- a trailing-quarters margin *trend* chart isn't really buildable from MarketDataLibrary yet. Transcript-reported margins (this quarter vs. prior quarter, when management states both) are the only real quarter-over-quarter source until quarterly financials exist. |
| 4 | Capital allocation | `cash_flow` Financing + Investing Activities (buybacks, dividends, debt issued/repaid, CapEx, M&A via Cash Acquisitions/Divestitures) | stacked bar / waterfall | **Not usable yet as of 2026-09-22**: `cash_flow` in MarketDataLibrary only has the Operating Activities table for ~5,035 of 5,039 symbols (confirmed on AAPL: 11 line items, all Operating Activities, nothing from Financing/Investing/Free Cash Flow/Additional Metrics). Same root cause as the balance_sheet gap (see MarketDataLibrary project memory) but not yet fixed. Until then, this section runs on transcript-extracted capital-return facts only (e.g. AAPL's call gives capital returned/dividends/buybacks directly) -- real, but only whatever the call happened to state, not a full CapEx/M&A picture. |
| 5 | Balance sheet snapshot | `financials` balance_sheet (Total Debt, Net Cash/Debt, Shareholders' Equity) | simple debt/cash/equity bar or net-debt gauge | **Not usable yet as of 2026-09-22**: same gap, ~5,042 of 5,050 symbols missing Liabilities/Equity data (fix written: `refetch_balance_sheet_liabilities.py` in MarketDataLibrary, not yet run at scale). Confirmed AAPL itself is one of the affected symbols. Fall back to whatever cash/debt figures the transcript states directly (AAPL's call gives cash & securities and total debt as reported facts) until the re-fetch runs. |
| 6 | Forward guidance | transcript facts (new guidance stated this call) | small table or delta vs. this quarter's actual | This is the "guided" half of section 1's next-quarter comparison; existing `trajectory` logic in `snapshot.py` already computes reported-vs-guided deltas -- reuse it rather than rebuilding. |
| 7 | Competitive environment | transcript only | text | No MarketDataLibrary source exists for this -- purely `signals.py` topic/stance extraction, same as today. |
| 8 | Product development | transcript only | text | Named products, launches, roadmap. No DB source -- distinct from section 2, see Design principles. |
| 9 | Macro/regulatory | transcript only | text | Kept separate from Competitive environment on purpose -- FX, tariffs, rates, and regulation are not "what a competitor did" and get conflated with competitive commentary if not their own bucket. |

## Open engineering work (not yet started)

- Matching a transcript sentence to the right `segments.item`/product name by text (e.g.
  recognizing "iPhone revenue was strong" refers to the `iPhone` segment) is a real
  text-matching problem, not a data problem -- this is where the actual engineering effort
  belongs, not in defining a product list upfront.
- Sections 3-5 are currently gated on MarketDataLibrary fixes that exist but haven't been run
  at scale: `refetch_balance_sheet_liabilities.py` (balance sheet), a not-yet-written
  equivalent for `cash_flow`'s Financing/Investing gap, and (longer-term) a real quarterly
  financials source, which doesn't exist in any form yet -- `financials` is annual+TTM only.
- A `segments` coverage check per symbol (does this company report at product/segment
  granularity at all?) should run before deciding whether section 2 can be DB-driven or needs
  to fall back to transcript-only, rather than assuming based on company size/prominence.

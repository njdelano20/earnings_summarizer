# Apple Inc. (AAPL) — June quarter 2026 — Quick Summary

_Build of the template in `summary_template.md`, reworked 2026-09-22 per direct design feedback
(layout, section cuts/additions, phrasing). Every number below is a real fact from
`output/AAPL_Q3_2026_facts.json` / `_snapshot.md` (fact IDs shown), a real SEC EDGAR filing
(`sec_edgar.py`), a real MarketDataLibrary row, or Alpha Vantage's INSIDER_TRANSACTIONS feed;
nothing is invented. Where a section's designed data source isn't usable yet, that's stated
plainly rather than silently backfilled from somewhere else._

## 1. Headline scoreboard

![Headline scoreboard](sample_AAPL_Q3_2026_scoreboard.png)

| Metric | Actual (June qtr) | vs. prior guidance |
|---|---|---|
| Revenue | $109.40B (+16% yoy) | not available — no AAPL call before this one in the dataset |
| EPS | $2.02/sh (+29% yoy) | not available |
| Gross margin | 50.1% (+80 bps qoq) | not available |
| Operating cash flow | $34.40B | — |

`AAPL_2026_3-001`, `AAPL_2026_3-013`, `AAPL_2026_3-006`, `AAPL_2026_3-015`

## 2. Business segments

![Revenue by segment](sample_AAPL_Q3_2026_segments.png)

| Segment | Revenue | Growth (yoy) | Gross margin |
|---|---|---|---|
| iPhone | $54.30B | +22% | — |
| Mac | $10.40B | +29% | — |
| iPad | $6.20B | -6% | — |
| Wearables, Home & Accessories | $7.90B | +6% | — |
| Services | $30.70B | +12% | 75.6% |

*Source note: this table is 100% transcript-extracted, not from MarketDataLibrary's
`segments` table — checked directly, AAPL has no `Revenue by Segment` category in `segments`
at all (only Revenue by Geography / EBIT by Geography / Gross Profit / Key Performance
Indicators). This is the real, current state, not a placeholder — for AAPL, this section
runs on the extractor alone.* `AAPL_2026_3-003`, `AAPL_2026_3-016`, `AAPL_2026_3-017`,
`AAPL_2026_3-018`, `AAPL_2026_3-005`, `AAPL_2026_3-010`

**What's driving each segment:**

- **iPhone** (+22% yoy): part of "an incredibly strong iPhone and Mac product cycle," with
  demand described as "beyond expectation." New iPhone 17 hardware (see Product development)
  is the direct driver.
- **Mac** (+29% yoy): the same product-cycle strength, plus the new MacBook Neo — also the
  hardware behind the education-channel Windows/Chromebook displacement noted in Competitive
  environment.
- **iPad** (-6% yoy): the one declining segment, attributed to a difficult compare against
  last year's iPad launch — a tough year-ago comparison, not a stated demand problem.
- **Wearables, Home & Accessories** (+6% yoy): modest growth; no specific driver was called
  out for this segment beyond the reported rate.
- **Services** (+12% yoy, 75.6% gross margin): records set "in every category," but margin
  fell 110 bps sequentially on product mix — the one segment where growth and margin moved
  in opposite directions this quarter.

**Margin context** (folded in here rather than a standalone trend section — see "what's still
open" below for why): company-wide gross margin rose to 50.1% (+80 bps qoq, headline
scoreboard); Services' own margin moved the *other* direction (-110 bps sequentially, mix-
driven, above), meaning the company-wide improvement came from elsewhere — hardware mix/ASPs
is the more likely source, though that specific attribution wasn't stated directly on the call.

## 3. Capital allocation

![Capital allocation](sample_AAPL_Q3_2026_capital.png)

| Use of cash | Amount (June qtr) | Source |
|---|---|---|
| Share repurchases | $25.80B | transcript |
| Dividends paid | $4.00B | transcript |
| CapEx | $2.46B | SEC 10-Q (derived — see note) |
| Debt repaid | $0.23B | SEC 10-Q (derived — see note) |
| Debt issued | $0 fiscal-year-to-date through this quarter | SEC 10-Q, filed directly |
| **Total returned to shareholders** (dividends + buybacks) | **$29.80B** | — |

`AAPL_2026_3-021`, `AAPL_2026_3-022`, `AAPL_2026_3-023`

**M&A** (deliberately not charted — a pie slice implies a completed cash outflow): AAPL's own
SEC filings show $0 tagged as acquisitions cash flow this fiscal year through the June
quarter, consistent with the call's characterization of its >$30B multi-year Broadcom
custom-silicon agreement as a forward supply commitment, not a completed acquisition with an
immediate cash outflow. Worth tracking going forward: if that changes to a real cash
acquisition in a future quarter, the SEC filing would show it, and it would belong in this
table.

*CapEx and debt activity are now real, SEC-filed figures (`sec_edgar.py`, extended
2026-09-22) instead of "not stated" placeholders. One real subtlety: SEC's cash-flow-statement
XBRL tags are cumulative-year-to-date only, not a discrete quarter (unlike revenue/EPS, which
get both) — this is SEC's own interim-reporting convention (ASC 270), confirmed directly
against AAPL's filed data. CapEx and debt-repaid above are DERIVED as (9-month YTD) minus
(6-month YTD), both real filed numbers, nothing fabricated — see `_derive_quarter_from_ytd()`
in `sec_edgar.py`. MarketDataLibrary's own `cash_flow` table remains Operating-Activities-only
for ~5,035/5,039 symbols (unchanged finding); SEC EDGAR now sidesteps that gap entirely for
the metrics it covers, so this section no longer depends on that fix being run.*

## 4. Balance sheet snapshot

![Balance sheet snapshot](sample_AAPL_Q3_2026_balance.png)

| | June quarter |
|---|---|
| Cash & securities | $147B |
| Total debt | $84B |
| Net cash | ~$63B |

`AAPL_2026_3-019`, `AAPL_2026_3-020`

*Designed source is `financials`' balance_sheet statement — not usable yet: AAPL is one of
the ~5,042 symbols still missing the Liabilities/Equity sections (fix exists —
`refetch_balance_sheet_liabilities.py` in MarketDataLibrary — running as of this writing, not
yet confirmed complete). Figures above are transcript-stated instead.*

## 5. Insider activity

**June quarter (2026-03-29 to 2026-06-27): $152.4M in insider stock sales, zero insider
purchases.**

| Insider | Title | Shares sold | Value | Avg. price |
|---|---|---|---|---|
| Arthur D. Levinson | Director | 300,000 | $86.74M | $289.14 |
| Timothy D. Cook | CEO | 131,576 | $33.54M | $254.94 |
| Deirdre O'Brien | SVP | 64,317 | $16.43M | $255.50 |
| Sabih Khan | COO | 33,317 | $8.52M | $255.63 |
| Jennifer Newstead | SVP, General Counsel | 16,238 | $4.81M | $296.42 |
| Kevan Parekh | CFO | 6,327 | $1.70M | $268.51 |
| Ben Borders | Principal Accounting Officer | 2,406 | $0.68M | $281.84 |

Most of this activity coincides with scheduled RSU-vesting dates for Cook, O'Brien, Khan,
Newstead, Parekh, and Borders — consistent with routine tax-withholding or pre-arranged
10b5-1 plan sales tied to equity compensation, not a fresh discretionary decision to sell.
The one exception: Director Arthur Levinson's $86.7M sale in early May has no corresponding
equity award that quarter, making it the more standalone data point here — though this feed
doesn't indicate whether it ran under an existing 10b5-1 plan, so it shouldn't be read as a
spontaneous signal on its own. No insider bought shares on the open market this quarter.

*Source: Alpha Vantage `INSIDER_TRANSACTIONS` (same provider as the transcript), fetched
2026-09-22. `add_insider_transactions_data.py` (new, in MarketDataLibrary) loads this into a
new `insider_transactions` table there for future automation — not yet run at scale (queued
behind the balance_sheet fix, which had the DB locked as of this writing).*

## 6. Forward guidance (September quarter)

| Metric | Guided |
|---|---|
| Revenue growth (yoy) | 9% to 11% |
| Gross margin | 47% to 48% |
| Operating expenses | $19.10B to $19.40B |
| Tax rate | ~16.5% |
| FX impact on revenue | ~2.5 pp unfavorable (total company) |

`AAPL_2026_3-025`, `AAPL_2026_3-029`, `AAPL_2026_3-031`, `AAPL_2026_3-033`, `AAPL_2026_3-024`

Both revenue growth (16% → 9-11%) and gross margin (50.1% → 47-48%) are guided down from
this quarter's actuals — the existing trajectory logic already flags both as "decelerating"
and "lower" respectively.

## 7. Competitive environment

AAPL reported IDC-sourced share gains in both iPhone and Mac this quarter — third-party data,
not an internal claim, though vendor-tracked share estimates carry their own measurement
uncertainty and are directional rather than precise. On Mac specifically, roughly half of
MacBook Neo's large-purchase volume with U.S. education institutions came at the direct
expense of Windows and Chromebook devices — a real displacement dynamic in a channel where
Apple has historically been a distant third choice. If it holds across future quarters, it
points to Mac's competitive position genuinely strengthening in a segment it hasn't typically
won; one quarter of education-channel data isn't enough on its own to call that a durable
trend rather than a one-time refresh cycle effect.

## 8. Product development

- **Siri AI rebuild** — a from-scratch redesign ("a completely reimagined version of Siri")
  integrated across Apple's platforms. The most consequential software item this quarter:
  Siri's AI competitiveness versus Google- and OpenAI-backed assistants elsewhere has been a
  recurring criticism, so this is Apple's direct answer to it. Not yet available in the EU
  (see Macro/regulatory) — a real near-term gap in one of Apple's largest markets.
- **MacBook Neo** — new hardware cited directly as a driver of Mac's +29% yoy growth (see
  Business segments), and of the education-channel Windows/Chromebook displacement (see
  Competitive environment).
- **iPhone 17** — new hardware cited as a driver of iPhone's +22% yoy growth.
- **AirPods Pro 3 / Max 2** — new audio hardware launched this quarter; no segment-level
  growth attribution was given for these specifically.
- **Apple Upgrade** — a new hardware-leasing/subscription program launched with Klarna. A
  business-model move (recurring revenue, lower upfront cost for buyers) rather than a
  product launch — worth watching for its effect on hardware-segment revenue recognition and
  upgrade-cycle attach rates in future quarters.
- **Broadcom custom-silicon agreement** — a >$30B multi-year commitment under Apple's U.S.
  manufacturing program. A supply-chain and onshoring move, not a completed acquisition (see
  Capital allocation: SEC filings show no acquisitions cash outflow this quarter, consistent
  with this being a forward commitment rather than a booked purchase).

## 9. Macro / regulatory

- **Tariffs** — a net tailwind this quarter ($0.11/sh EPS benefit, ~2pp gross-margin
  benefit), guided to shrink to ~1pp of margin benefit next quarter. A fading tailwind rather
  than a new headwind for now, but one whose benefit is explicitly decaying quarter over
  quarter.
- **FX** — a growing headwind: ~2.5pp on the Services guide for next quarter, ~5pp across the
  broader March-to-September stretch. Directionally negative for margin into the next
  quarter, on top of the tariff tailwind fading at the same time.
- **Supply/memory costs** — flagged as worsening into the September quarter, with memory
  cost cited as the larger driver of the margin guide-down (ahead of FX). A cost-side
  pressure that could persist beyond one quarter if industry-wide memory pricing stays
  elevated, rather than a one-quarter blip.
- **Regulatory** — the EU is the one region-specific friction point: Siri AI has not shipped
  there yet. That means Apple's most consequential software launch this quarter (see Product
  development) is currently unavailable in one of its largest markets — a real, ongoing
  revenue/engagement gap specific to that feature and that region, not yet resolved.

---

## Source documents

- [Full earnings call transcript](../data/transcripts/AAPL_Q3_2026.txt)
- [10-Q filed with the SEC](https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm)
  (period ended 2026-06-27, filed 2026-07-31)

---

## What this demonstrates, and what's still open

**Works today, real data, no placeholders**: headline numbers, business segments (transcript
side), capital allocation (now mostly SEC-sourced), insider activity (new), forward guidance,
competitive environment, product development, macro/regulatory.

**This round's rework (2026-09-22), per direct design feedback**: cut the standalone margin-
trend section (too thin on 1-2 points to earn its own section; the real margin story is now
folded into Business segments, where it's actually explained). CapEx and debt issued/repaid
are now real SEC-filed numbers instead of "not stated" — required extending `sec_edgar.py` to
derive a standalone quarter from two YTD filings, a genuine and reusable capability, not a
one-off. Capital allocation is now a pie chart. Added a new Insider activity section (Alpha
Vantage `INSIDER_TRANSACTIONS`, a new provider for this project). Sections 7-8 rewritten to
read as analysis ("here's what's happening, here's the plausible read") rather than reported
speech ("management said"); Product development restructured from one paragraph into one
bullet per product.

**Still blocked on MarketDataLibrary fixes**: balance sheet snapshot (fix exists, running as
of this writing — see Balance sheet snapshot section). Segments coverage for AAPL specifically
still runs on the extractor alone (no `Revenue by Segment` rows in the library for this
symbol) — a per-symbol reality, not a bug.

**Not started**: the actual sentence-to-segment matching engine that would replace this
hand-assembly with real code — still the real remaining engineering item for section 2. Two-
column (chart-and-text-side-by-side) layout is a presentation concern, not a content one — see
the rendered HTML version of this doc rather than this Markdown source for that.

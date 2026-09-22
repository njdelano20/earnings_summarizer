# Batch 2026Q2

Updated 2026-09-21T23:39. 43 symbols: 40 ok, 3 no_transcript.

- Calls processed: 40; `db_eligible` (recommendation only, nothing is loaded anywhere): 37
- Facts: 859 (high 561, medium 148, low 150); figures left unclaimed: 1980
- Review queue: 2278 rows in review_queue.csv

## Needs attention

| Symbol | Fiscal | Facts | Unclaimed | Flags |
|---|---|---|---|---|
| ABT | 2026Q2 | 7 | 35 | many_unclaimed_figures; no_segment_map |
| AIG | 2026Q2 | 40 | 80 | many_unclaimed_figures; no_segment_map |
| AMZN | 2026Q2 | 24 | 39 | many_unclaimed_figures; no_segment_map |
| AXP | 2026Q2 | 22 | 61 | many_unclaimed_figures; no_segment_map |
| BAC | 2026Q2 | 49 | 83 | many_unclaimed_figures; no_segment_map |
| CAT | 2026Q2 | 23 | 63 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| CMCSA | 2026Q2 | 23 | 28 | no_segment_map |
| COST | 2026Q3 | 15 | 58 | many_unclaimed_figures; no_segment_map |
| CVX | 2026Q2 | 6 | 33 | many_unclaimed_figures; no_segment_map |
| DAL | 2026Q2 | 19 | 48 | period_unchecked; many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| DHR | 2026Q2 | 18 | 28 | many_unclaimed_figures; no_segment_map |
| DIS | 2026Q3 | 4 | 18 | few_facts; many_unclaimed_figures; no_segment_map |
| GE | 2026Q2 | 37 | 90 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| GOOGL | 2026Q2 | 42 | 40 | no_segment_map |
| GS | 2026Q2 | 27 | 46 | many_unclaimed_figures; no_segment_map |
| HESM | 2026Q2 | 21 | 12 | gold_fail |
| HON | 2026Q2 | 12 | 73 | many_unclaimed_figures; no_segment_map |
| JNJ | 2026Q2 | 12 | 52 | many_unclaimed_figures; no_segment_map |
| JPM | 2026Q2 | 13 | 59 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| LLY | 2026Q2 | 16 | 44 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| MCD | 2026Q2 | 13 | 32 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| MET | 2026Q2 | 15 | 115 | many_unclaimed_figures; no_segment_map |
| META | 2026Q2 | 18 | 26 | no_segment_map |
| MSFT | 2026Q4 | 67 | 50 | no_segment_map |
| NEE | 2026Q2 | 8 | 40 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| NKE | 2026Q4 | 22 | 41 | many_unclaimed_figures; no_segment_map |
| PFE | 2026Q2 | 15 | 50 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| PG | 2026Q4 | 21 | 68 | many_unclaimed_figures; no_segment_map |
| PLD | 2026Q2 | 2 | 66 | few_facts; many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| PYPL | 2026Q2 | 15 | 45 | many_unclaimed_figures; no_segment_map |
| RTX | 2026Q2 | 11 | 70 | many_unclaimed_figures; no_segment_map |
| SCHW | 2026Q2 | 9 | 57 | many_unclaimed_figures; no_segment_map |
| UNH | 2026Q2 | 8 | 59 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| UPS | 2026Q2 | 46 | 47 | no_segment_map |
| V | 2026Q3 | 27 | 52 | many_unclaimed_figures; no_segment_map |
| VICI | 2026Q2 | 8 | 12 | gold_fail |
| VZ | 2026Q2 | 29 | 51 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| WFC | 2026Q2 | 46 | 51 | no_segment_map |
| XOM | 2026Q2 | 4 | 21 | few_facts; many_unclaimed_figures; no_segment_map; assumed_calendar_year |

## Not done

| Symbol | Status | Detail |
|---|---|---|
| HD | no_transcript | No transcript returned for HD 2026Q2. |
| NVDA | no_transcript | No transcript returned for NVDA 2027Q2. |
| WMT | no_transcript | No transcript returned for WMT 2027Q2. |

## Spot-check

Open audit_sample.csv and compare each fact with its sentence. Check the LABELS as well as the number: is it the right segment (not the whole company), the right period, reported vs guidance, and the right basis (year-over-year vs sequential)? Most real errors are a correct number with a wrong label. Type `ok` or `wrong` in the `verdict` column, then run `py batch.py --audit-report`.

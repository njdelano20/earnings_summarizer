# Batch 2026Q2

Updated 2026-09-21T15:35. 23 symbols: 20 ok, 3 no_transcript.

- Calls processed: 20; `db_eligible` (recommendation only, nothing is loaded anywhere): 17
- Facts: 403 (high 260, medium 81, low 62); figures left unclaimed: 964
- Review queue: 1107 rows in review_queue.csv

## Needs attention

| Symbol | Fiscal | Facts | Unclaimed | Flags |
|---|---|---|---|---|
| CAT | 2026Q2 | 23 | 63 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| COST | 2026Q3 | 15 | 58 | many_unclaimed_figures; no_segment_map |
| DAL | 2026Q2 | 19 | 48 | period_unchecked; many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| GE | 2026Q2 | 37 | 90 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| HESM | 2026Q2 | 18 | 15 | gold_fail |
| JPM | 2026Q2 | 13 | 59 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| LLY | 2026Q2 | 16 | 44 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| MCD | 2026Q2 | 13 | 32 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| MSFT | 2026Q4 | 67 | 50 | no_segment_map |
| NEE | 2026Q2 | 8 | 40 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| NKE | 2026Q4 | 22 | 41 | many_unclaimed_figures; no_segment_map |
| PFE | 2026Q2 | 15 | 50 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| PG | 2026Q4 | 21 | 68 | many_unclaimed_figures; no_segment_map |
| PLD | 2026Q2 | 2 | 66 | few_facts; many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| UNH | 2026Q2 | 8 | 59 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| V | 2026Q3 | 27 | 52 | many_unclaimed_figures; no_segment_map |
| VICI | 2026Q2 | 1 | 20 | few_facts; many_unclaimed_figures; gold_fail |
| VZ | 2026Q2 | 29 | 51 | many_unclaimed_figures; no_segment_map; assumed_calendar_year |
| XOM | 2026Q2 | 4 | 21 | few_facts; many_unclaimed_figures; no_segment_map; assumed_calendar_year |

## Not done

| Symbol | Status | Detail |
|---|---|---|
| HD | no_transcript | No transcript returned for HD 2026Q2. |
| NVDA | no_transcript | No transcript returned for NVDA 2027Q2. |
| WMT | no_transcript | No transcript returned for WMT 2027Q2. |

## Spot-check

Open audit_sample.csv and compare each fact with its sentence. Check the LABELS as well as the number: is it the right segment (not the whole company), the right period, reported vs guidance, and the right basis (year-over-year vs sequential)? Most real errors are a correct number with a wrong label. Type `ok` or `wrong` in the `verdict` column, then run `py batch.py --audit-report`.

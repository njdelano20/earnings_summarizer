# Batch 2026Q1

Updated 2026-09-21T15:47. 3 symbols: 2 ok, 1 error.

- Calls processed: 2; `db_eligible` (recommendation only, nothing is loaded anywhere): 1
- Facts: 61 (high 33, medium 13, low 15); figures left unclaimed: 73
- Review queue: 101 rows in review_queue.csv

## Needs attention

| Symbol | Fiscal | Facts | Unclaimed | Flags |
|---|---|---|---|---|
| CPRT | 2026Q3 | 45 | 15 | gold_fail |
| WMT | 2027Q1 | 16 | 58 | many_unclaimed_figures; no_segment_map |

## Not done

| Symbol | Status | Detail |
|---|---|---|
| HD | error | Alpha Vantage: We have detected your API key as ***REDACTED*** and our standard API rate limit is 25 requests per day. Please subscribe to any of the premium plans at https://www.alphavantage.co/premium/ to instantly remove all daily rate limits. |

## Spot-check

Open audit_sample.csv and compare each fact with its sentence. Check the LABELS as well as the number: is it the right segment (not the whole company), the right period, reported vs guidance, and the right basis (year-over-year vs sequential)? Most real errors are a correct number with a wrong label. Type `ok` or `wrong` in the `verdict` column, then run `py batch.py --audit-report`.

# Batch 2026Q2

Updated 2026-09-21T14:56. 3 symbols: 3 ok.

- Calls processed: 3; `db_eligible` (recommendation only, nothing is loaded anywhere): 1
- Facts: 64 (high 46, medium 16, low 2); figures left unclaimed: 72
- Review queue: 90 rows in review_queue.csv

## Needs attention

| Symbol | Fiscal | Facts | Unclaimed | Flags |
|---|---|---|---|---|
| HESM | 2026Q2 | 18 | 15 | gold_fail |
| VICI | 2026Q2 | 1 | 20 | few_facts; many_unclaimed_figures; gold_fail |

## Spot-check

Open audit_sample.csv and compare each fact with its sentence. Check the LABELS as well as the number: is it the right segment (not the whole company), the right period, reported vs guidance, and the right basis (year-over-year vs sequential)? Most real errors are a correct number with a wrong label. Type `ok` or `wrong` in the `verdict` column, then run `py batch.py --audit-report`.

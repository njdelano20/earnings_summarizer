# Batch 2026Q1

Updated 2026-09-21T14:56. 1 symbols: 1 ok.

- Calls processed: 1; `db_eligible` (recommendation only, nothing is loaded anywhere): 0
- Facts: 18 (high 10, medium 2, low 6); figures left unclaimed: 42
- Review queue: 50 rows in review_queue.csv

## Needs attention

| Symbol | Fiscal | Facts | Unclaimed | Flags |
|---|---|---|---|---|
| CPRT | 2026Q3 | 18 | 42 | many_unclaimed_figures; gold_fail |

## Spot-check

Open audit_sample.csv and compare each fact with its sentence. Check the LABELS as well as the number: is it the right segment (not the whole company), the right period, reported vs guidance, and the right basis (year-over-year vs sequential)? Most real errors are a correct number with a wrong label. Type `ok` or `wrong` in the `verdict` column, then run `py batch.py --audit-report`.

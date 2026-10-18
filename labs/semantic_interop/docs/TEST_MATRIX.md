# Test matrix and measured outcome

| Path / case family | Count | Evidence | Observed status |
|---|---:|---|---|
| Native: eight base queries | 8 | Official compilation + independent SQL oracle | PASS |
| Native: zero share, year boundary, four empty slices | 6 | Official compilation + independent SQL oracle | PASS |
| MSI to Ossie to MSI | 14 | Real conversion; static contract drift; compiler initialization fails | SEMANTIC_MISMATCH; execution NOT_EXECUTED |
| Microsoft emitted revenue/customer count | 2 | Real conversion; emitted DAX inspected | NOT_EXECUTED |
| Microsoft placeholder measures | 4 | Real conversion; warnings and BLANK annotations | UNSUPPORTED |
| Microsoft relationship inventory | 1 | Two source endpoints absent in output | SEMANTIC_MISMATCH |
| Authored TMDL measures | 8 | Existing bounded static inspection only | 3 NOT_EXECUTED; 5 UNSUPPORTED |
| Active expectation versus inactive shipping relationship | 1 | Existing relationship checker | SEMANTIC_MISMATCH; controlled |
| Fault SQL | 6 | Real DuckDB execution, independently authored mutations | NUMERICAL_MISMATCH; controlled |
| Duplicate product key | 1 | SQL uniqueness check on transactional mutation | INVALID_MODEL; controlled |

The eight TMDL measures include two controlled unsupported constructs
(`ISCROSSFILTERED` and `USERELATIONSHIP`). They are included in the ten controls,
not added again to the 51-record total. Unsupported here may mean the **repository's
bounded grammar** cannot inspect a valid DAX expression; it is not a claim that Power
BI cannot execute that function. Authored counts include `COALESCE` to express the
intended empty-input zero, also outside the bounded grammar.

## Controlled numerical differences

| Mutation | Oracle | Observed faulty output |
|---|---:|---:|
| SUM customer key instead of distinct count | 5 | 1012 |
| Item count at order grain | 8 | 12 |
| Order totals joined back to items | 265.30 | 510.60 |
| Removed completed-order filter | 195.30 | 265.30 |
| Mean of daily ratios instead of ratio of sums | 0.7361477573 | 0.6666666667 |
| Disconnected filter fails to propagate | 195.30 | 265.30 |

All ten controlled failures are detected; none are currently undetected or untestable.
The report distinguishes these categories so future added cases cannot quietly count
as detections. Static detection of unsupported DAX is not numerical validation.
Round-trip semantic losses are detected by the harness while the upstream converter
reports no issues. Their numerical effects remain untestable in the unmodified chain.

## Automated validation

- 26 lab tests cover reviewed oracle outputs, actual integrations, exact decimals,
  nulls, row/column ordering, duplicate multiplicity, dimension-key matching,
  floating tolerance including overlapping matches, missing evidence and status rules.
- The integration tests execute the benchmark twice in fresh subprocesses with a
  Python audit hook rejecting socket connection and DNS requests, including dbt children.
  Reports and portable evidence hashes must be identical.
- The existing public audit, demos, regression suite and post-run provenance checks
  run through `scripts/check.py`. The environment's console scripts precede global
  installations on PATH.
- Additional CI runs the same checks on Windows and Ubuntu, Python 3.12. Local
  implementation validation was on Windows; remote CI has not been triggered.

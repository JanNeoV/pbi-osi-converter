# Benchmark contract

Scope: synthetic business-query equivalence on one fixed shop fixture. Eight base
queries use six metric definitions; category and time are query dimensions, not
additional separately authored metrics. Six edge slices bring each numerical path
to 14 cases.

## Data and meaning

Four customer dimension rows, eight orders, twelve items and four products include
repeated customers, a null customer key, an unmatched customer key, a no-item order,
two unmatched product relationships, a null amount and a zero-valued category.
Amounts use `DECIMAL(18,2)`. The fixed daily calendar covers 2024-02-28 through
2026-01-03. Order and shipping dates deliberately differ.

No currency conversion, tax, discount or status-based exclusion is implicit.
Revenue includes all non-null item amounts across every order status. Missing
product relationships remain in a null category group. Order count includes the
order with no items. Purchasing customers counts five distinct non-null keys on
orders, including key 999, whether or not a customer dimension row exists.

| Query | Independent expected baseline |
|---|---|
| Revenue | 265.30 |
| Order count | 8 |
| Average order value | 265.30 / 8 = 33.1625 |
| Category revenue | Books 85.10; Tools 170.00; Zero 0.00; null 10.20 |
| Daily revenue | Feb 28 2024: 40; Feb 29: 20; Mar 1: 150; Dec 31 2025: 0.30; Jan 1 2026: 50; Jan 2: 5 |
| Completed revenue | 195.30 |
| Completed share | 195.30 / 265.30, approximately 0.7361477573 |
| Purchasing customers | 5 |

Empty scalar sums return null; counts return zero; undefined ratios return null.
Zero-category share is null. The half-open interval `[2025-12-31, 2026-01-02)`
returns 50.30. The empty interval is before 2020-01-01. No calendar densification
is requested for grouped revenue: absent fact dates do not become fabricated rows.

Reference calculations are maintained in `fixtures/oracle`. Tests independently
assert the reviewed values above. Ratios use DuckDB floating division; monetary sums
remain exact decimals. The dbt item view only attaches order date/status at item
grain; it does not compute oracle totals.

## Comparison and evidence

Columns are matched by explicit names. Only declared compiler aliases are renamed.
Midnight, timezone-free timestamps in the daily dimension may normalize to dates;
non-midnight timestamps are rejected. Row ordering is ignored; duplicate multiplicity
and missing rows are preserved. Dimension keys determine eligible row matches.

Integers and decimals compare exactly; an exact expected number becoming a float is
a mismatch. Floating results use `abs_diff <= 1e-9 + 1e-9 * abs(reference)`. Non-finite
values fail comparison. Relative difference is null when the reference is zero.
Null never equals zero. JSON cells carry explicit types; decimal and integer values
are strings, preserving their precision.

The static contract checks aggregation, expression, metric filters, recursive ratio
inputs, declared entities and default time dimensions. It is deliberately bounded;
matching these signatures is not a general proof of DAX or SQL equivalence.

Structural validity refers to the relevant parser/model shape, not runtime usability.
Ossie uses official Pydantic validation; TMDL inspection is a repository parser, not
TOM validation. Final status precedence is invalid structure, unsupported construct,
executed numerical mismatch, detected semantic mismatch, missing evidence, then pass.
The independent execution-status field prevents static failures from looking executed.

Reports include input hashes, exact installed versions, the Ossie commit, artifact
hashes, typed row sets and diagnostics. Raw dbt logs/manifests remain in the generated
run directory. The portable bundle uses the parsed normalized source manifest and
retains converter output unchanged apart from host-path redaction. No timestamps or
run-directory names enter the portable report. Two runs in the same locked environment
must produce identical reports and evidence hashes.

The [report schema](report.schema.json) constrains statuses and forbids `PASS` without
execution, numerical agreement, valid structure and a positive bounded semantic check.

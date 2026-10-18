# Power BI candidate: business metrics and presentation logic

## Task 04 official round trip

The [Task 04 capture](../../../../results/task04/powerbi-001/report.json) runs the
unmodified official Microsoft converter in both directions on two minimal,
explicitly authored TMSL inputs derived from this candidate. The inputs retain the
canonical 12 sessions and six orders through the same CSV-backed M expressions.
They expose only **Conversion Rate** and **Paid Revenue**. The two count measures
required by Conversion Rate are hidden dependencies.

| Case | Authored input | Actual Apache Ossie output | Actual reconstructed Power BI model |
|---|---|---|---|
| A: business metrics | [authored.model.bim](../../../../results/task04/powerbi-001/case-a/authored.model.bim) | [generated.ossie.yml](../../../../results/task04/powerbi-001/case-a/generated.ossie.yml) | [reconstructed.model.bim](../../../../results/task04/powerbi-001/case-a/reconstructed.model.bim) |
| B: dynamic SWITCH | [authored.model.bim](../../../../results/task04/powerbi-001/case-b/authored.model.bim) | [generated.ossie.yml](../../../../results/task04/powerbi-001/case-b/generated.ossie.yml) | [reconstructed.model.bim](../../../../results/task04/powerbi-001/case-b/reconstructed.model.bim) |

Reproduce from the repository root with the unchanged baseline environment:

```powershell
.\.tmp\semantic-lab-venv\Scripts\python.exe labs/semantic_interop/scripts/run_powerbi_showcase.py --output .tmp/task04-powerbi-fresh
```

The destination must not exist. The runner authors this one fixture's TMSL from
explicit columns and relationships, copying the accepted DAX/M expressions; it is
not a general TMDL parser. It calls `convert_semantic_model_to_ossie` followed by
`convert_ossie_to_semantic_model` without intervening edits. The installed packages
are `apache-ossie==0.2.0.dev0` and `apache-ossie-microsoft==0.2.0.dev0`, pinned to
Apache Ossie commit `6ebc5e5612467dac405985f44ea078b39c891bee`.

Both conversions completed and their Ossie documents pass the official Pydantic
schema. All nine bounded structure checks pass in A; all eleven pass in B.
Table/column structure, four/five measure expressions, measure dependencies, both
active many-to-one relationships, `oneDirection` filtering, M expressions,
partitions, static formats and the dynamic format definition survive. Complete
documents compare equal after normalizing only named-object ordering and legal
TMSL string versus line-array serialization. No model property is manually
reconstructed after conversion. See each `validation.json`, `warnings.json` and
`converter.log` beside the artifacts.

Each run preserves one official warning: shared Power Query expressions and
parameters have no Ossie core counterpart and are kept in the Power BI stash.
The output uses explicit `DAX` expressions and `POWER_BI` extensions. Preserved
DAX is **PRESERVED_VENDOR_SPECIFIC**, and must not be described as portable SQL.
Column names/types, keys and relationship endpoints are **PORTABLE** structural
properties in this bounded experiment. M, filter direction, display formats and
home-table metadata are **PRESERVED_VENDOR_SPECIFIC**. Cross-engine execution of
the DAX/SWITCH logic is **UNSUPPORTED** by this measured lane. Target engine
validation, numerical results and visual rendering are **NOT_EXECUTED**.

### SWITCH limits and optional Power BI execution

Case B has exactly one disconnected two-row KPI Selector and one numeric dynamic
measure. `Selected KPI` depends on the two headlines; Conversion Rate depends on
Converted Sessions and Eligible Sessions. Its exact expression survives:

```dax
SWITCH(
    SELECTEDVALUE('KPI Selector'[KPI], "Conversion Rate"),
    "Conversion Rate", [Conversion Rate],
    "Paid Revenue", [Paid Revenue],
    BLANK()
)
```

The [prospective Query View checks](validate-task04.dax) cover a single choice,
no selection and both choices selected, each overall and with Direct/Indirect
filters: **12 DAX cases, all NOT_EXECUTED**. No/multiple selections are expected
to use the explicit Conversion Rate default, as documented by Microsoft's
[SELECTEDVALUE reference](https://learn.microsoft.com/en-us/dax/selectedvalue-function-dax).
The disconnected selector changes the branch; channel and session-month context
still apply through the preserved relationships and `KEEPFILTERS` predicates.

| Context | Rate choice, none or multiple: expected value/display | Revenue choice: expected value/display |
|---|---|---|
| Overall | 0.4 / 40.0% | 390 / 390.00 EUR |
| Direct | 0.5 / 50.0% | 300 / 300.00 EUR |
| Indirect | 0.25 / 25.0% | 90 / 90.00 EUR |

These are independent oracle expectations, not captured Power BI results. The
dynamic format DAX also survives in `POWER_BI` extensions; Microsoft documents
that [dynamic format strings preserve numeric values](https://learn.microsoft.com/en-us/power-bi/create-reports/desktop-dynamic-format-strings)
and recommends checking them in a visual. Percent and EUR are different units;
an unchanged SWITCH expression does not make the dynamic measure portable.

To continue locally, use a separate working copy of either authored or
reconstructed BIM, set `FixtureRoot` to the absolute `conversion_demo/data`
directory, load it with a TOM-capable tool into a local Power BI model, refresh,
then paste the applicable sections into [DAX Query View](https://learn.microsoft.com/en-us/power-bi/transform-model/dax-query-view).
Retain raw results separately for each input and output model; check the final
section in a visual with Display units set to None. The original TMDL candidate
below remains available for authorship context.

The actual TOM probe reports the optional `tom` extra unavailable in the baseline
environment. TOM validation is **NOT_EXECUTED**, so JSON/schema/inventory checks
do not establish engine model validity. DAX parsing, refresh, all 12 selection
tests, runtime filter semantics, formatting and target-to-oracle comparison remain
**NOT_EXECUTED**. No runtime or package was installed to fill that evidence gap.
Official Microsoft references above were checked 2026-10-18.

## Task 03 authored candidate and original runtime boundary

**Manually authored candidate. It is not Apache Ossie converter output. Power BI,
TOM parsing, refresh, DAX validation queries and visual-format checks are
`NOT_EXECUTED`.** Repository inventory checks inspect text and relationships only;
they do not establish model validity in a Microsoft engine or numerical equivalence.
Actual official converter artifacts are retained separately by the Stage B runner.

The two business tables are Sessions and Orders. Sales Channel is a derived
dimension, and KPI Selector is a disconnected presentation table. They add no
synthetic business transactions. Before a future local refresh, set `FixtureRoot`
in `definition/expressions.tmdl` to the absolute canonical `conversion_demo/data`
directory. The M queries use those same CSV files; no credentials are required.
This is a TMDL definition candidate, not a complete report or deployed PBIP project.

## Core business contract and filter context

Use `'Sales Channel'[sales_channel]` and `Sessions[session_month]` as business
dimensions. The one-direction relationships carry channel filters into Sessions
and Orders. Orders cannot filter Sessions back to only sessions with orders.
`KEEPFILTERS` intersects eligibility, paid-status and cutoff predicates with the
existing context. It does not remove the channel or session-month filter.

The fixed observation cutoff is **2026-03-01 exclusive**. A qualifying paid order
must belong to an eligible session and have `order_date < cutoff`; sessions must
start on or after 2026-01-01 and precede the cutoff. Every order is attributed to its one originating session
and that session's one channel. Monthly Paid Revenue is **session-cohort revenue**:
January 270 EUR and February 120 EUR. Transaction-date revenue is a separate metric
question (January 190 EUR; February 200 EUR), not implemented as a hidden change of grain.
This fixture has whole-order observed statuses, not a status-event history or a
production revenue-recognition policy.

Converted Sessions counts distinct paid `Orders[session_id]`; Paid Order Count
counts distinct paid `Orders[order_id]`. A second paid order in an already converted
session changes order count, revenue and AOV, while leaving conversion unchanged.
Counts explicitly return zero on an empty slice. Empty sums remain BLANK, and
`DIVIDE` has no alternate result, so zero-denominator ratios remain BLANK.

## Dynamic selection is Power BI presentation logic

Selected KPI uses `SWITCH(SELECTEDVALUE(..., "Conversion Rate"), ...)`. One selected
value chooses its metric. No selection leaves all three selector values visible;
multiple selections also leave more than one value. Both therefore choose the
documented Conversion Rate default. A channel filter never filters this disconnected
table, but still affects the selected underlying business metric.

| Selection | Overall value to verify | Format to verify |
|---|---:|---|
| Conversion Rate | 0.4 | 40.0% |
| Paid Revenue | 390 | 390.00 EUR |
| Eligible Sessions | 10 | 10 |
| None or multiple | 0.4 | 40.0% |

Counts are whole numbers, revenue uses fixed decimal input, and ratios are numeric
division results. The numeric selector may use a common numeric representation
across branches; its dynamic format string supplies the appropriate unit. It does
not call `FORMAT` to turn measure values into text. Set visual Display units to None
when checking exact formatting. This selector and its dynamic formatting have no
assumed portable equivalent in Ossie or Databricks.

[validate.dax](validate.dax) supplies target queries for each single selection, no
selection, multiple selections, channel interactions, monthly cohorts, an empty
cohort and a nonempty cohort with zero conversions. The listed answers are the
oracle contract to check, **not captured DAX results**. A future runtime validation
must execute these queries, retain actual results and compare them with the shared
oracle. It must also check dynamic formatting in a visual. To exercise the optional
multiple-order variant, append `data/additional_order.csv` to the Orders input in a
separate copy of the model: expect conversion 40%, paid orders 5, revenue 420 EUR,
and AOV 84 EUR.

## Official references

The candidate uses compatibility level 1601 for measure dynamic formatting, following
the [TMDL view compatibility example](https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-tmdl-view).
[TMDL syntax](https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview)
does not itself validate an engine model. Microsoft documents
[SELECTEDVALUE's default for zero or multiple values](https://learn.microsoft.com/en-us/dax/selectedvalue-function-dax),
[KEEPFILTERS intersection](https://learn.microsoft.com/en-us/dax/keepfilters-function-dax),
[DIVIDE's BLANK default](https://learn.microsoft.com/en-us/dax/divide-function-dax), and
[dynamic formatting that preserves numeric values](https://learn.microsoft.com/en-us/power-bi/create-reports/desktop-dynamic-format-strings).
References checked 2026-10-17.

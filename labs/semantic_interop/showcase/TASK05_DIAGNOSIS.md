# Task 05: what the nine Task 04 paths actually establish

This is a read-only diagnosis of the sealed [Task 04 validation](../results/task04-showcase/validation.json),
[nine-path comparisons](../results/task04-showcase/paths.json), actual artifacts and converter notices.
It does not change the historical statuses. New Task 05 experiments have separate evidence.
Task 04 measured **8 conversions producing output, 1 converter failure, 6 static semantic mismatches,
2 bounded native-definition passes, and 0 converted numerical passes**.

The source reference is executed MetricFlow SQL against the unchanged Task 03 sessions/orders fixture,
independently checked by raw-table DuckDB SQL: Conversion Rate **0.4**, Paid Revenue **390.00 EUR**;
Direct **0.5 / 300.00 EUR**, Indirect **0.25 / 90.00 EUR**.
See the actual [native results and independent oracle](../results/task04-showcase/native/report.json).

## Exact nine-path classification

Categories below use the task's vocabulary. Multiple categories can apply to one path.
“Preserved” concerns the inspected definitions; it never implies target execution.

| Path through Ossie | Exact observed reason for Task 04 status | Classification | Evidence and limit |
|---|---|---|---|
| MetricFlow → MetricFlow | Both converters emit parseable artifacts. Source spine count 1 becomes 0; both models lose aggregation-time defaults and all entities. All 11 requested headline queries stop at `At least one time spine must be configured to use the semantic layer, but none were found.` | Missing execution configuration; lost or altered business definition | [Outbound inventory](../results/task04-showcase/metricflow/to-ossie/leg.json), [returned inventory](../results/task04-showcase/to-metricflow/metricflow/leg.json), [actual execution attempts](../results/task04-showcase/to-metricflow/metricflow/execution.json). No returned numerical result exists. |
| Power BI → Power BI | Complete normalized source/target documents, DAX, relationships, columns, M and formatting metadata compare equal. Shared Power Query expressions are in a `POWER_BI` extension. | Vendor-specific semantics preserved but not portable; missing target runtime | [Definition comparison and TOM/DAX boundary](../results/task04-showcase/powerbi/core/report.json), [warning](../results/task04-showcase/powerbi/core/to-ossie/warnings.json). No TOM validation, refresh or DAX execution occurred. |
| Databricks → Databricks | Native source SQL, filter, dimensions and measures survive. The source SQL loses its final newline; all parsed values compare equal after trimming only final source whitespace. | Vendor-specific semantics preserved but not portable; missing target runtime | [Exact comparison scope](../results/task04-showcase/databricks/databricks-roundtrip/definition-comparison.json), [Ossie](../results/task04-showcase/databricks/databricks-roundtrip/imported.ossie.yml). `filter: is_eligible = true` is retained through a `DATABRICKS` extension. No Databricks analyzer/query ran. |
| MetricFlow → Power BI | Nine measures, including both headlines, become `BLANK()`. The exporter warns that CASE-filtered aggregates and compound/ratio SQL exceed its elementary SQL-to-DAX subset; original expressions remain annotations. The relationship is skipped because physical `session_id` is not an exported field name. | Unsupported semantic construct; lost or altered business definition; missing target runtime | [Warnings](../results/task04-showcase/to-powerbi/metricflow/warnings.json), [actual BIM](../results/task04-showcase/to-powerbi/metricflow/output.model.bim). This is a demonstrated definition change, not an observed DAX numerical failure. |
| Power BI → MetricFlow | The returned expressions literally contain `DIVIDE`, `CALCULATE`, `DISTINCTCOUNT`, bracketed DAX references and DAX filter context. No DAX-to-SQL translation or M partition reconstruction occurs. All 11 attempts stop first at missing time spine. | Vendor-specific semantics preserved but not portable; unsupported semantic construct; missing execution configuration | [Returned manifest](../results/task04-showcase/to-metricflow/powerbi/output.manifest.json), [execution attempts](../results/task04-showcase/to-metricflow/powerbi/execution.json). A later DAX/SQL error is unobserved because initialization stops first. |
| MetricFlow → Databricks | Output exists, but expressions contain `session__is_eligible` and `order__order_status`; neither token is a raw/derived column or generated dimension. The tokens first appear in MetricFlow → Ossie and remain in Databricks output. Export emits 12 notices, including dropping joined-table dimensions and time-role metadata. | Unsupported semantic construct; lost or altered business definition; missing target runtime | [Ossie input](../results/task04-showcase/metricflow/to-ossie/output.ossie.yml), [generated YAML](../results/task04-showcase/databricks/metricflow-to-databricks/generated.metric-view.yml), [notices](../results/task04-showcase/databricks/metricflow-to-databricks/from-ossie.stderr.log). Unresolved references are demonstrated statically; a Databricks analyzer rejection is not observed. |
| Databricks → MetricFlow | Databricks' eligibility filter remains only in its vendor extension and is absent from every returned metric filter. The return adapter splits the SQL source text at `.` into relation-name components (`database: SELECT s`); it does not create an executable source query. Missing time spine stops all 11 attempts first. | Lost or altered business definition; unsupported semantic construct; missing execution configuration | [Comparison with returned filters/source](../results/task04-showcase/paths.json), [returned manifest](../results/task04-showcase/to-metricflow/databricks/output.manifest.json), [execution attempts](../results/task04-showcase/to-metricflow/databricks/execution.json). No numerical mismatch was executed. |
| Power BI → Databricks | Java export exits with `Dataset 'Sessions': source 'Sessions' must be a 3-part catalog.schema.table identifier or a SELECT/WITH subquery`. No target is emitted. Power BI's real M import binding is vendor metadata; the generic source is the table name. | Converter execution failure; missing execution configuration; vendor-specific semantics preserved but not portable | [Actual stderr](../results/task04-showcase/databricks/powerbi-to-databricks/from-ossie.stderr.log), [Ossie input](../results/task04-showcase/powerbi/core/to-ossie/output.ossie.yml). Expression translation is not reached; further semantic failures are hypotheses. |
| Databricks → Power BI | All six measures become annotated `BLANK()`. The eligibility-filter extension is explicitly dropped; the computed month column also becomes `BLANK()`. SQL query source needs an import partition. | Unsupported semantic construct; lost or altered business definition; missing target runtime | [Warnings](../results/task04-showcase/to-powerbi/databricks/warnings.json), [actual BIM](../results/task04-showcase/to-powerbi/databricks/output.model.bim). Even elementary `COUNT(DISTINCT session_id)` cannot resolve: `session_id` is in source SQL but not among the two exported dimension fields. |

Power BI and Databricks target execution were unavailable. MetricFlow/DuckDB is available locally;
its returned models are blocked by demonstrated model/configuration loss, not by a missing local runtime.
Parsing YAML/JSON succeeds for eight paths; that criterion alone cannot establish business correctness.

## Three minimized transformation chains

**MetricFlow → Power BI.** These fragments are extracts of the actual Task 04 output, not a replacement model:

```yaml
# MetricFlow → Ossie: paid_revenue_base
expression: SUM(CASE WHEN session__is_eligible = true AND order__order_status
  = 'paid' THEN orders.amount_eur END)
```

```json
{"name":"paid_revenue","expression":"BLANK()",
 "annotations":[{"name":"OssieExpressionDialect","value":"ANSI_SQL"}]}
```

The full target also retains the original expression in its `OssieExpression` annotation.
The first unequivocal target measure change is SQL-to-DAX fallback to `BLANK()`;
the upstream unresolved filter tokens and aliased key references are independent prior defects.
The Microsoft adapter states the supported boundary in its warnings: an aggregate over one column
can translate, while the required CASE/compound/ratio expressions cannot.
There is also a source-binding gap: missing workspace/item identifiers yield Direct Lake placeholders.

**MetricFlow → Databricks.** The canonical source uses MetricFlow's real dimension resolver:

```yaml
# fixtures/conversion_demo/dbt/models/metrics.yml
filter: "{{ Dimension('session__is_eligible') }} = true AND {{ Dimension('order__order_status') }} = 'paid'"
```

The official dbt adapter's filter stubs render the two logical tokens literally;
`_collect_filter_sql` explicitly says it does not resolve physical SQL column aliases.
The generated Ossie marks that expression `ANSI_SQL`. Java export removes a supported root qualifier
such as `sessions.session_id`, but leaves the two logical tokens above untouched.
The generated dimensions are `session`, `session_date`, `sales_channel`, `is_eligible`, `session_count`.
Thus the first incompatible transformation is logical filter references becoming unresolved SQL tokens
in the outbound Ossie, before Databricks generation. Renaming these by hand would create a separate repair
experiment. It would not demonstrate unchanged adapter interoperability.

**Power BI → Databricks.** The actual Ossie binding is:

```yaml
- name: Sessions
  source: Sessions
  custom_extensions:
  - vendor_name: POWER_BI
    data: '{"_v": 2, "partitions": [{"mode": "import", "name": "Sessions", "source": {"expression": "SessionsData", "type": "m"}}]}'
```

The shared `SessionsData` Power Query expression is held in the model's `POWER_BI` extension.
The Java exporter validates the generic source and fails before interpreting the measure expressions.
A namespace mapping alone would not establish DAX translation, Power Query execution or equivalent filters.
The source adapter retained those definitions for a Power BI return; the Databricks adapter has no demonstrated
consumer of that stash.

## SWITCH is a separate preservation result

The actual [selector expression](../results/task04-showcase/powerbi/switch/to-ossie/output.ossie.yml) is a
first-class Ossie metric with dialect **DAX**:

```dax
SWITCH(
    SELECTEDVALUE('KPI Selector'[KPI], "Conversion Rate"),
    "Conversion Rate", [Conversion Rate],
    "Paid Revenue", [Paid Revenue],
    BLANK()
)
```

The metric names, DAX text, selector schema and ordinary relationships are inspectable outside the stash.
The selector's two rows are the M `#table(...)` partition in a dataset `POWER_BI` extension.
Dynamic format DAX and home-table placement are in the measure's `POWER_BI` extension;
shared M expressions and additional model metadata are in the model extension.
The native round trip preserves the complete normalized document and the dependencies
`Selected KPI → Conversion Rate, Paid Revenue`; the selector remains disconnected.
See [bounded checks](../results/task04-showcase/powerbi/switch/report.json).

Another platform could inspect the DAX expression, named dependencies, table schema and relationships,
or implement an explicit consumer for vendor metadata. These converters do not demonstrate such a consumer.
MetricFlow returns the DAX as a metric expression and does not recreate the M rows/selection interaction.
Databricks fails even earlier because `KPI Selector` is unreachable from `Sessions`:
[actual failure](../results/task04-showcase/databricks/powerbi-switch-to-databricks/from-ossie.stderr.log).
It never tests whether SWITCH can translate.

The [12 selector expectations](../results/task04-showcase/powerbi/oracle/report.json) come from independent
DuckDB values and the intended selector contract. Actual DAX values are absent.
Without opening, refreshing and querying a real DAX model, slicer behavior, no-selection/multiselection
defaults, filter propagation, numeric values and dynamic display formats remain **NOT_VERIFIED**.
Expression retention and executable semantic interoperability are distinct claims.

## What the earlier diagnoses establish

[Task 02](../docs/ARTICLE_DRAFT.md) independently minimized three mechanisms:

1. Exported field `order` with physical expression `order_id`, but primary key `order_id`.
   The reverse adapter compares the key with `field.name`, so the entity disappears.
   The same endpoint defect causes Microsoft relationships to be skipped.
2. Aggregate SQL such as `SUM(CASE...)` can return as a SUM metric whose expression is itself an aggregate,
   causing nested aggregation. A separately evaluated upstream PR437 addressed that mechanism.
3. MetricFlow-qualified filter references remain unresolved SQL aliases even after that aggregate repair.

Task 02's 11/14 numerical successes used a **local key repair plus explicit reconstruction of spine and time
defaults**. They are neither unchanged official conversions nor results on this Task 03 sessions/orders fixture.
[Task 03](../docs/TASK03_VALIDATION.md) measured 31 native oracle matches but zero unchanged round-trip
executions; Task 04's headline subset has the same first initialization blocker. Task 05 must not relabel
the historical reconstructed successes as an official happy path.

## Bounded reproducibility checks

From the repository root, use the existing pinned environment and a fresh temporary output directory:

```powershell
& .tmp/semantic-lab-venv/Scripts/python.exe -m pytest -q labs/semantic_interop/tests --basetemp .tmp/pytest-task05-lab-001
```

This is the relevant existing suite: source/oracle fixture, platform boundaries, minimized historical
diagnoses, Databricks exporter evidence and hash-bound Task 04 custody. The historical Task 04 run was
84 passing tests; a fresh Task 05 result must be recorded separately.
The parent regression command is `python -m pytest -q -m "not live_agent"`; the historical result was
165 passed / 1 skipped. No new test framework, converter patch or canonical fixture mutation is required.

All Python Ossie adapters used here are version `0.2.0.dev0` at commit
`6ebc5e5612467dac405985f44ea078b39c891bee` ([installed-source audit](../results/task04-showcase/supplemental/converter-source-audit.json)).
Java Databricks is `0.1.0-SNAPSHOT` at `53418ba8a4b8a20951334fdb9d9638aa34b514e3`
([build and executable provenance](../results/task04-showcase/databricks/databricks-roundtrip/provenance.json)).
No upstream source, pinned environment or historical result was modified for this diagnosis.

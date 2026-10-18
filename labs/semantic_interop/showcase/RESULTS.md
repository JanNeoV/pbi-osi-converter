# Verified results and limitations

**The original MetricFlow model matches the independent DuckDB oracle. In this lab, no unchanged converted model has a numerically verified happy path.** Overall results are **40% Conversion Rate and 390 EUR Paid Revenue**; Direct **50% / 300 EUR**, Indirect **25% / 90 EUR**. [Executed source SQL, typed results and oracle](../results/task04-showcase/native/report.json) support these values.

This matrix summarizes the nine investigated Task 04 routes through Apache Ossie. It preserves the classifications in the sealed [machine-readable paths](../results/task04-showcase/paths.json) and [validation record](../results/task04-showcase/validation.json). Each row separates four claims; a conversion PASS does not imply a static or numerical PASS.

| Status | Meaning in the relevant column |
|---|---|
| PASS | The stated check ran and passed. Static PASS is limited to the inspected definitions. |
| MISMATCH | A concrete semantic/configuration difference was observed, or executed values disagree when used for a numerical check. |
| FAIL | The converter/check ran and failed; no usable output for that leg was emitted. |
| BLOCKED | A prerequisite or initialization failure prevents the stated execution/comparison. |
| NOT_EXECUTED | The target query did not run; no target result is implied. |
| UNKNOWN | Evidence cannot establish the stated claim. |

In **Native target queries**, BLOCKED means MetricFlow initialization was attempted but no requested query could execute; the underlying case records say `execution_status: NOT_EXECUTED`. For Power BI and Databricks no native target runtime was exercised. **Oracle agreement** concerns the converted target, independently of the source's measured agreement.

| Direction through Ossie | Conversion completed | Static semantic preservation | Native target queries | Numerical oracle agreement | Observed boundary and direct evidence |
|---|---|---|---|---|---|
| MetricFlow → MetricFlow | PASS | MISMATCH | BLOCKED | BLOCKED | Return loses time spine, entities and aggregation-time defaults. All 11 headline query attempts stop at missing spine. [Return inventory](../results/task04-showcase/to-metricflow/metricflow/leg.json), [attempts](../results/task04-showcase/to-metricflow/metricflow/execution.json). |
| Power BI → Power BI | PASS | PASS | NOT_EXECUTED | BLOCKED | Complete normalized DAX, relationships, columns, partitions, M and formatting metadata retained, including vendor extensions. No Desktop/TOM/refresh/DAX pass. [Comparison and runtime boundary](../results/task04-showcase/powerbi/core/report.json). |
| Databricks → Databricks | PASS | PASS | NOT_EXECUTED | BLOCKED | Complete parsed YAML values retained after ignoring only trailing whitespace of source SQL. No native creation/analyzer/query. [Comparison scope](../results/task04-showcase/databricks/databricks-roundtrip/definition-comparison.json), [conversion report](../results/task04-showcase/databricks/databricks-roundtrip/report.json). |
| MetricFlow → Power BI | PASS | MISMATCH | NOT_EXECUTED | BLOCKED | Nine measures, including both headlines, become annotated `BLANK()`; relationship skipped. This is a definition change, not a measured DAX numerical failure. [Warnings](../results/task04-showcase/to-powerbi/metricflow/warnings.json), [BIM](../results/task04-showcase/to-powerbi/metricflow/output.model.bim). |
| Power BI → MetricFlow | PASS | MISMATCH | BLOCKED | BLOCKED | DAX copied as expressions without SQL translation or M reconstruction. All 11 attempts stop first at missing spine; any subsequent DAX failure is unobserved. [Manifest](../results/task04-showcase/to-metricflow/powerbi/output.manifest.json), [attempts](../results/task04-showcase/to-metricflow/powerbi/execution.json). |
| MetricFlow → Databricks | PASS | MISMATCH | NOT_EXECUTED | BLOCKED | Unresolved `session__is_eligible` / `order__order_status` survive; notices describe dropped constructs. Analyzer rejection has not been observed. [Generated YAML](../results/task04-showcase/databricks/metricflow-to-databricks/generated.metric-view.yml), [notices](../results/task04-showcase/databricks/metricflow-to-databricks/from-ossie.stderr.log). |
| Databricks → MetricFlow | PASS | MISMATCH | BLOCKED | BLOCKED | Global eligibility filter not reconstructed; SQL source split into dotted relation-name components. All 11 attempts stop first at missing spine. [Manifest](../results/task04-showcase/to-metricflow/databricks/output.manifest.json), [attempts](../results/task04-showcase/to-metricflow/databricks/execution.json). |
| Power BI → Databricks | FAIL | UNKNOWN | NOT_EXECUTED | BLOCKED | Export rejects logical source `Sessions`: requires three-part name or SELECT/WITH source. No target file; expression translation is not reached. [Actual stderr](../results/task04-showcase/databricks/powerbi-to-databricks/from-ossie.stderr.log), [failed leg](../results/task04-showcase/databricks/powerbi-to-databricks/from-ossie.leg.json). |
| Databricks → Power BI | PASS | MISMATCH | NOT_EXECUTED | BLOCKED | All six measures and computed month become `BLANK()`; eligibility extension dropped; SQL source lacks runnable import binding. [Warnings](../results/task04-showcase/to-powerbi/databricks/warnings.json), [BIM](../results/task04-showcase/to-powerbi/databricks/output.model.bim). |

Totals describe different checks: **8 conversions emitted output, 1 converter failed; 6 static MISMATCH, 2 bounded static PASS, 1 UNKNOWN; 0 converted numerical PASS**. These are not an overall converter pass rate. Parseable artifacts do not establish correct execution.

## Historical evidence

Task 05 reran small changes on the same canonical fixture. MetricFlow source revenue changed **390 → 440 EUR**, Conversion Rate remained **40%**, and channel revenue changed **300 / 90 → 300 / 140 EUR**. [Baseline execution](../results/task05-minimal/metricflow/baseline/source/execution.json), [changed execution](../results/task05-minimal/metricflow/include-refunded/source/execution.json), and [returned-target attempt](../results/task05-minimal/metricflow/include-refunded/target/execution.json) distinguish a measured source edit from the blocked target. The refund text reaches Databricks YAML with the same unresolved identifiers; Power BI receives `BLANK()`.

Native Databricks baseline/changed definitions survive its own adapter ([comparisons](../results/task05-minimal/databricks/baseline/comparison.json), [changed comparison](../results/task05-minimal/databricks/include-refunded/comparison.json)); the monetary values in its [oracle results](../results/task05-minimal/databricks/oracle-results.json) were executed in DuckDB, not in Databricks. Power BI candidate models are **NOT_VERIFIED** in Desktop; retained DAX text is not DAX runtime validation.

Power BI's disconnected selector and `SWITCH` are additional diagnostics, not a tenth matrix row. Its native [definition comparison](../results/task04-showcase/powerbi/switch/report.json) preserves DAX, dependencies and selector metadata. The [Databricks attempt](../results/task04-showcase/databricks/powerbi-switch-to-databricks/from-ossie.stderr.log) fails first on the disconnected join graph, before testing any DAX translation. The [selector expectations](../results/task04-showcase/powerbi/oracle/report.json) use independent oracle values and an intended contract; they are not executed DAX results.

Task 03's [31 native query cases](../docs/TASK03_VALIDATION.md) include technical edge cases and must not be combined with Task 04's headline subset. Task 02's [11/14 repaired and reconstructed successes](../docs/TASK02_VALIDATION.md) used a different earlier fixture, a local key repair, and explicit spine/default reconstruction; they do not demonstrate unchanged official conversion. See [Task 05 diagnosis](TASK05_DIAGNOSIS.md) for minimized transformation chains and evidence limits.

Continue with the [hands-on tutorial](TUTORIAL.md) or [Charlotte's Databricks handoff](DATABRICKS_HANDOFF.md). A new result needs actual target SQL/DAX, raw values, versions, warnings/errors, every manual intervention and an independent oracle comparison.

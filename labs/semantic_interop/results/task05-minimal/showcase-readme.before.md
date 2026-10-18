# One dataset. Two metrics. Three platforms.

**Conversion Rate: 40% overall · 50% Direct · 25% Indirect**  
**Paid Revenue: 390 EUR**

These are executed MetricFlow/DuckDB results checked against the independent
[DuckDB oracle](../results/task04-showcase/native/report.json). The unchanged
[fixture](../fixtures/conversion_demo/) has 12 sessions and six orders.
**Definition preservation works on two native round trips; numerical
interoperability across the nine paths remains unverified.**

## 1. Same Platform Round Trips

Follow each source through its actual, unedited converter outputs.

| Source → Apache Ossie → Target | Result |
|---|---|
| [MetricFlow](../results/task04-showcase/native/source.manifest.json) → [Ossie](../results/task04-showcase/metricflow/to-ossie/output.ossie.yml) → [MetricFlow](../results/task04-showcase/to-metricflow/metricflow/output.manifest.json) | Conversion completes. Time spine, entities and default time settings lost; execution blocked. |
| [Power BI](../results/task04-showcase/powerbi/core/source.model.bim) → [Ossie](../results/task04-showcase/powerbi/core/to-ossie/output.ossie.yml) → [Power BI](../results/task04-showcase/powerbi/core/from-ossie/output.model.bim) | Exact inspected native definitions preserved. DAX execution **UNKNOWN**. |
| [Databricks](../results/task04-showcase/databricks/databricks-roundtrip/source.metric-view.yml) → [Ossie](../results/task04-showcase/databricks/databricks-roundtrip/imported.ossie.yml) → [Databricks](../results/task04-showcase/databricks/databricks-roundtrip/roundtrip.metric-view.yml) | Definitions preserved, ignoring final SQL whitespace. Engine execution **UNKNOWN**. |

Power BI and Databricks retain vendor metadata. Preservation of a vendor
definition does not establish portable, executable business semantics.

## 2. Cross-Platform Conversions

Every path uses the same business contract and the native sources above.
The target link opens the generated file, or the actual failure when none exists.

| Source → Apache Ossie → Target | Result |
|---|---|
| MetricFlow → [Ossie](../results/task04-showcase/metricflow/to-ossie/output.ossie.yml) → [Power BI](../results/task04-showcase/to-powerbi/metricflow/output.model.bim) | Headline measures become `BLANK()`. |
| Power BI → [Ossie](../results/task04-showcase/powerbi/core/to-ossie/output.ossie.yml) → [MetricFlow](../results/task04-showcase/to-metricflow/powerbi/output.manifest.json) | DAX copied as metric expressions; no SQL translation. |
| MetricFlow → [Ossie](../results/task04-showcase/metricflow/to-ossie/output.ossie.yml) → [Databricks](../results/task04-showcase/databricks/metricflow-to-databricks/generated.metric-view.yml) | Unresolved dimension references remain. |
| Databricks → [Ossie](../results/task04-showcase/databricks/databricks-roundtrip/imported.ossie.yml) → [MetricFlow](../results/task04-showcase/to-metricflow/databricks/output.manifest.json) | Eligibility filter lost; query source misinterpreted as relation components. |
| Power BI → [Ossie](../results/task04-showcase/powerbi/core/to-ossie/output.ossie.yml) → [Databricks](../results/task04-showcase/databricks/powerbi-to-databricks/from-ossie.stderr.log) | **FAIL:** `Sessions` source binding unsupported. |
| Databricks → [Ossie](../results/task04-showcase/databricks/databricks-roundtrip/imported.ossie.yml) → [Power BI](../results/task04-showcase/to-powerbi/databricks/output.model.bim) | Headline measures become `BLANK()`. |

## 3. The SWITCH Exception

One numeric KPI selector chooses **Conversion Rate** or **Paid Revenue** using
`SWITCH(SELECTEDVALUE(...), ...)`.

| Source → Apache Ossie → Target | Result |
|---|---|
| Power BI SWITCH → [Ossie](../results/task04-showcase/powerbi/switch/to-ossie/output.ossie.yml) → [Power BI](../results/task04-showcase/powerbi/switch/from-ossie/output.model.bim) | **PASS for preservation:** DAX, dependencies, disconnected two-choice table and dynamic format metadata survive. Execution **UNKNOWN**. |
| Power BI SWITCH → Ossie → [MetricFlow](../results/task04-showcase/to-metricflow/switch/output.manifest.json) | DAX text and selector schema survive; executable selection and table data do not. |
| Power BI SWITCH → Ossie → [Databricks](../results/task04-showcase/databricks/powerbi-switch-to-databricks/from-ossie.stderr.log) | **FAIL:** disconnected selector is unreachable from `Sessions`. |

This demonstrates **preserved vendor-specific DAX**, with no working portable
selector demonstrated. [Twelve expected selection cases](../results/task04-showcase/powerbi/oracle/report.json)
are retained; actual DAX values are unobserved. Native alternatives and their
execution limits are discussed in the [technical notes](../docs/TASK04_SHOWCASE.md).

Reproduce from the repository root, using a new output directory:

```powershell
& .tmp/semantic-lab-venv/Scripts/python.exe labs/semantic_interop/scripts/run_showcase.py --output .tmp/ossie-demo-001
```

[Charlotte's handoff](DATABRICKS_HANDOFF.md) ·
[Separate direction records, versions, warnings and hashes](../results/task04-showcase/report.json) ·
[Validation and preservation](../docs/TASK04_SHOWCASE.md)

**Final verified-path matrix.** Every row goes through Apache Ossie.
Conversion PASS means both converters emitted parseable artifacts. Semantic
PASS* means inspected native definitions survive; target execution remains
UNKNOWN. Numerical BLOCKED means no completed target/oracle comparison.

| Source → Target | Conversion | Semantic preservation | Numerical equivalence |
|---|---|---|---|
| MetricFlow → MetricFlow | PASS | MISMATCH | BLOCKED |
| Power BI → Power BI | PASS | PASS* | BLOCKED |
| Databricks → Databricks | PASS | PASS* | BLOCKED |
| MetricFlow → Power BI | PASS | MISMATCH | BLOCKED |
| Power BI → MetricFlow | PASS | MISMATCH | BLOCKED |
| MetricFlow → Databricks | PASS | MISMATCH | BLOCKED |
| Databricks → MetricFlow | PASS | MISMATCH | BLOCKED |
| Power BI → Databricks | FAIL | UNKNOWN | BLOCKED |
| Databricks → Power BI | PASS | MISMATCH | BLOCKED |

# Task 04: complete nine-path showcase evidence

The manager entry point is the separate [showcase](../showcase/README.md).
This append-only continuation fills the missing native Databricks and reverse
cross-platform paths without rewriting existing Task 02/03/04 captures or fixtures.
The actual [machine-readable comparison](../results/task04-showcase/paths.json)
contains nine paths. Each points to two independently recorded directions.

## Reproduction and pins

From the repository root, use the existing [Task 03 setup](../README.md#original-milestone-commands)
and run:

```powershell
& .tmp/semantic-lab-venv/Scripts/python.exe labs/semantic_interop/scripts/run_showcase.py --output .tmp/ossie-demo-001
```

Use a new destination: captures refuse overwrites. This thin coordinator reuses
the Task 03 benchmark, compiler, typed comparator, oracle and official APIs. It
does not translate expressions, restore lost configuration, or deploy models.
It retains failures and continues independent converter routes. Tests cover
missing-artifact assessment and rejection of an unverified Java JAR.

The Python Ossie/core, dbt and Microsoft packages are all pinned to
`6ebc5e5612467dac405985f44ea078b39c891bee`, version `0.2.0.dev0`.
Their installed VCS metadata is checked before conversion. An independent
[source audit](../results/task04-showcase/supplemental/converter-source-audit.json)
also matches all 17 installed Python converter files against pinned Git blobs,
normalizing only CRLF line endings and retaining both raw hashes. The Java adapter is
the separately built official `0.1.0-SNAPSHOT` at
`53418ba8a4b8a20951334fdb9d9638aa34b514e3`; its JAR SHA-256 is
`09af8c0868cd56eac6a2de0e07dd59c1288506d4dd5779c9db2d3fbcec1e490f`.
Its historical 100-test Maven build is retained explicitly as historical build
evidence, alongside fresh source commit/tree/clean-status checks. No new Maven
test run is claimed. The [handoff](../showcase/DATABRICKS_HANDOFF.md) contains
exact Java commands and fresh-build instructions. No dependency pins changed.

## Business contract and equivalent native sources

Only Conversion Rate and Paid Revenue are public business metrics. The unchanged
canonical fixture's count dependencies and supporting validation measures remain
internal. Ratios are recomputed from distinct eligible/converted sessions at each
grain; revenue belongs to the originating session cohort, before the exclusive
March 1 order cutoff. Overall 40%/390 EUR alone is insufficient: channel and
session-month slices expose denominator, filter, join and date-role mistakes.

The fresh native MetricFlow gate executes **31/31** queries matching independent
DuckDB SQL, including **11/11** headline cases, and detects **5/5** injected
teaching faults. Those controls are not converter defects. Power BI TMSL is
explicit native authorship transcribed using the existing fixture builder;
its source parameter is bound before conversion. Databricks starts from the
existing native Metric View fixture, copied byte-for-byte. These sources are
not generated target outputs. A separate
[source sanity audit](../results/task04-showcase/supplemental/source-audit.json) executes the native
Databricks source SQL/expressions in **DuckDB only**, matching the same 11 cases;
Power BI intent receives static review only. Neither is target-engine evidence.

## What each claim means

Conversion PASS requires actual converter execution plus parseable output. It
does not require a target engine to accept the file. Ossie artifacts pass the
official core schema; Databricks YAML export parsing does not run its analyzer.
Conversion FAIL records the actual official error, not a guessed limitation.
NOT_EXECUTED is used when no converter runs or an intermediate has no numerical
engine evaluation.

Semantic PASS is bounded to complete inspected native definition comparison:
Power BI normalized model equality, and Databricks YAML equality ignoring only
trailing whitespace of the entire SQL source. Exact inequality and both source
hashes remain available. Engine semantics remain UNKNOWN. MISMATCH requires
concrete lost configuration, filters or expressions. Copied vendor dialect text
alone never earns a portability PASS.

Numerical PASS requires executed target query results matching the independent
typed oracle. All nine converted paths are **BLOCKED**: the MetricFlow targets
fail initialization because the unchanged return has no time spine; no authorized
Power BI/XMLA or Databricks runtime session was supplied. Raw per-case failures
are retained in `to-metricflow/*/execution.json`. Power BI and Databricks engine
execution is NOT_EXECUTED, with no invented values or numerical mismatches.
No successful numerical comparison is claimed for a converted target.

The Power BI-to-Databricks export uses official `--source Sessions`, preserving
the intended session denominator. It fails the source identifier requirement.
The SWITCH export fails earlier on graph reachability. Neither is reported as
an observed DAX translation failure. The two SQL-to-Power BI exports instead
actually produce annotated BLANK() headline measures.

## SWITCH: replay versus a native alternative

The [SWITCH assessment](../results/task04-showcase/switch-assessment.json)
separates three target paths. Power BI replay preserves its DAX expression,
dependencies, disconnected two-row M table, relationships and dynamic format
metadata exactly. TOM is unavailable; model load, refresh, DAX execution and
visual formatting are NOT_EXECUTED. Executability is UNKNOWN. Twelve planned
none/single/multiple selections across overall/Direct/Indirect retain executed
DuckDB expectations and null actual DAX results. No/multiple selection should
default to Conversion Rate under the authored SELECTEDVALUE expression.

MetricFlow receives the literal SWITCH DAX expression and selector schema, but
no M partition data or executable selector interaction. Its target initialization
is actually attempted and blocked. The unchanged Databricks exporter rejects the
disconnected table. Thus this experiment demonstrates no portable selector.

This is not a claim that other platforms cannot offer KPI choice. The official
[MetricFlow query interface](https://docs.getdbt.com/docs/build/metricflow-commands)
selects named metrics with `--metrics`; a caller could switch the queried metric.
That is an inference about a separate presentation interaction, not a DAX
translation or an implementation tested here. Databricks documents parameters
usable in measure expressions and dashboard widgets on compatible compute,
requiring DBR 18.2+ or a suitable SQL warehouse.
([Official parameter documentation](https://docs.databricks.com/aws/en/uc-semantics/metric-views/use-parameters),
checked 2026-10-18.) A manually authored parameter/CASE design could therefore
represent KPI choice; reproducing Power BI default/multiple-selection behavior
and dynamic percentage/EUR formatting would need separate design and execution.
Its status here is NOT_EXECUTED. No replacement translator or native alternative
is inserted into the measured paths.

## Validation and custody

The [sealed validation record](../results/task04-showcase/validation.json)
contains exact commands, regression results, input preservation and independent
hash checks. The root [report](../results/task04-showcase/report.json) hashes
converter outputs, query results and separate direction diagnostics. Supplemental
checks and their [final hashes](../results/task04-showcase/seal.json) are separate,
so the original run report stays intact.
Existing Task 02/03/04 evidence and every canonical fixture file are preserved.
No push, merge, cloud deployment or publication was performed.

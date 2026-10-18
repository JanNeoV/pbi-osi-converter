# Upstream compatibility review — Task 02

Research date: 2026-10-17. The baseline is Ossie `6ebc5e5612467dac405985f44ea078b39c891bee`;
current upstream is frozen at `ef91cb61092c60f4b918016a81cfdbe483e113bc`.
The three intervening commits change only NVIDIA. Core, dbt and Microsoft source are unchanged.
Current upstream was nevertheless installed and executed separately, with the baseline runtime dependencies.
The [review record](../labs/semantic_interop/results/task02/upstream-review/review.json) records changed paths,
issue states, PR heads and ancestry checks. These are dated observations, not live status promises.

The causal code is in the pinned [MSI exporter](https://github.com/apache/ossie/blob/ef91cb61092c60f4b918016a81cfdbe483e113bc/converters/dbt/src/ossie_dbt/msi_to_ossie.py)
(`_extract_keys`, `_build_entity_index`), [MSI importer](https://github.com/apache/ossie/blob/ef91cb61092c60f4b918016a81cfdbe483e113bc/converters/dbt/src/ossie_dbt/ossie_to_msi.py)
(`convert`, `_classify_field`, `_convert_metric`), and [filter renderer](https://github.com/apache/ossie/blob/ef91cb61092c60f4b918016a81cfdbe483e113bc/converters/dbt/src/ossie_dbt/filter_utils.py).
MetricFlow's installed `metricflow_semantics/time/time_spine_source.py` is hash-bound in the diagnostic report.
It accepts both `project_configuration.time_spines` and legacy `time_spine_table_configurations`, then raises when both are empty.

| Construct | Baseline/current behavior | Upstream overlap | Decision |
|---|---|---|---|
| Time-spine project configuration | One source spine becomes none; MSI parses but MetricFlow lookup fails | [#318](https://github.com/apache/ossie/pull/318) discusses nonportable time semantics; it is not a project-configuration restoration API | Classify CONFIGURATION_GAP; explicit Level 3 only |
| Entity names versus expressions | Keys/endpoints name physical expressions; fields name logical entities; reverse conversion loses entities | [#315](https://github.com/apache/ossie/pull/315), open; [#466](https://github.com/apache/ossie/issues/466), [#486](https://github.com/apache/ossie/pull/486) name-resolution discussion | Evaluate forward-side field-name repair and report compatibility implications |
| Repeated physical expression | PR315's reverse-side expression matching also promotes a measure to a primary entity | PR315 source hunks at `b9a7c28469834c3ba1f80dc4b88f5f75322eb416` | New regression evidence against the proposed approach, not a claim about merged upstream |
| Compound aggregate | Returned SIMPLE metric contains an aggregate expression plus an outer SUM | [#430](https://github.com/apache/ossie/issues/430), [#437](https://github.com/apache/ossie/pull/437), open | PR437 at `04d960191e691750c88a6f5f6d39a9bdadb7b7dd` fixes the minimized case; do not duplicate it |
| Metric filter identifiers | A MetricFlow dimension token survives as an unbound SQL column | Still reproduced with PR437; separate from its aggregation fix | Retain separate minimized evidence; no speculative filter compiler |
| Derived offsets/time grains | Offsets are not covered by this benchmark's numerical cases; return code assigns DAY | [#317](https://github.com/apache/ossie/issues/317), [#318](https://github.com/apache/ossie/pull/318), open | Known limitation; no new numerical claim |
| Expression dialect preference | Preferred dialect, then OSSIE_SQL_2026, then fallback | [#464](https://github.com/apache/ossie/pull/464) merged, already in baseline; [#461](https://github.com/apache/ossie/issues/461) remains open | Do not present the merged preference fix as new |
| Metadata | Some labels/configuration are absent on return; empty attributes remain UNKNOWN | [#303](https://github.com/apache/ossie/issues/303) | Keep metadata loss distinct from executed result changes |
| Calculated-table DAX measures | Two authored probe measures import as metrics and return once to their original host | [#444](https://github.com/apache/ossie/issues/444) closed by [#564](https://github.com/apache/ossie/pull/564); merge `a1605a5dd715ab1adcc492057656b493d95b97e0` is in baseline | Existing fix verified statically; unrelated to the four SQL placeholders |
| Power BI round-trip fidelity | Direct DAX, references, display metadata and authored relationship survive the probe | [#448](https://github.com/apache/ossie/pull/448), merged and already in baseline | No DAX execution evidence |

The first reproducible environment for the lab failures is Python 3.12.10, MetricFlow 0.213.0,
dbt-metricflow 0.15.0, dbt Core 1.12.6, dbt-duckdb 1.11.0, DuckDB 1.5.6 and the baseline Ossie commit.
This establishes an affected version, not the historical introduction commit.

Upstream test results: current dbt **176 passed**, local key repair **179 passed**, PR437 **185 passed**;
each suite retains five passing snapshots. Logs are retained in the review/repair evidence directories.
PR315 was evaluated by applying exactly its three source hunks to current upstream; its historical full suite
was not claimed to have been run. PR437 was evaluated through its pinned dbt source in an isolated environment;
the core and Microsoft packages remained current and their source versions are recorded.

See [findings](SEMANTIC_LOSS_FINDINGS.md), [contribution candidate](OSSIE_CONTRIBUTION_CANDIDATE.md),
and the [generated comparison tables](../labs/semantic_interop/docs/DIFFERENTIAL_EVALUATION.md).

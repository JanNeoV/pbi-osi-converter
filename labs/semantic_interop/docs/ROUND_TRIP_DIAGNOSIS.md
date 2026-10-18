# Round-trip diagnosis

The blocker is a chain of independent losses, not a missing DuckDB table.
The source dbt project builds `time_spine`; its semantic manifest contains one modern `time_spines` entry with
relation, day column and granularity. The return converter constructs `PydanticProjectConfiguration()` and carries none of it.
The returned MSI passes Pydantic parsing, whose schema permits empty spine lists. MetricFlow 0.213.0 rejects it when
`SemanticManifestLookup` unconditionally builds its time-spine sources.

Both modern and legacy source configurations construct a lookup successfully. An otherwise empty manifest fails
with the same missing-spine message, establishing a runtime prerequisite beyond this fixture. Source code hashes,
constructor signature, exceptions, source configuration and returned configuration are in `semantic_diagnostics.time_spine`.

| Experiment | Intervention | Pinned/current outcome | Local key-repair outcome |
|---|---|---|---|
| Level 1 | None | Missing time spine, 0 executions | Missing time spine, 0 executions |
| Level 2 | Separate documented external configuration | Not executed: no independent spine argument in this pinned API | Same boundary |
| Level 3 modern | Copy only source `time_spines` | Missing primary entity, 0 executions | Missing aggregation time default, 0 executions |
| Level 3 legacy | Reconstruct equivalent legacy spine entry | Missing primary entity, 0 executions | Missing aggregation time default, 0 executions |
| Level 3 spine + defaults | Copy source spine and per-model temporal defaults | Missing primary entity, 0 executions | 11 executions, all matching oracle; 3 blocked |

The key repair is separately installed converter code. No entity, filter, metric expression or relationship is copied
into the converted manifest by the diagnostic runner. Every reconstruction has a parent hash, resulting manifest hash,
and exact before/after ledger. The original returned artifact is retained separately and never modified.

The temporal-default reconstruction is semantic metadata supplied from the source; it is not evidence that Ossie carried
that metadata. Accordingly all Level 3 manifests explicitly disclaim a lossless-round-trip claim.
Parsing, successful lookup, successful SQL generation, successful execution and oracle agreement are separate observations.

Reproduce with the commands in the [lab README](../README.md). Inspect
[sealed experiment artifacts](../results/task02/verified-003/report.json) and
[generated numerical tables](DIFFERENTIAL_EVALUATION.md). The original reproducer still uses historical evidence unchanged.

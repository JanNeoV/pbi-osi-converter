# Semantic loss findings — Task 02

The initial benchmark is preserved. The accepted result is a bounded diagnosis and partial repair:
**11 of 14 round-trip queries execute and match the independent oracle after the local entity-key fix
and explicitly reconstructed time configuration.** Neither the pinned nor current unmodified round trip executes.

| Finding | Classification | Evidence and consequence |
|---|---|---|
| Time spine omitted | CONFIGURATION_GAP | Return code creates an empty project configuration. Modern and legacy source spines are accepted by MetricFlow; an empty manifest reproduces the prerequisite failure too. This is not specific to the shop fixture. |
| Default aggregation time omitted | EXPRESSIVENESS_LIMITATION | Ossie time flags do not carry the default time dimension. After the key repair, restoring the spine alone exposes the missing temporal default. |
| Entity/key namespace mismatch | CONVERTER_DEFECT | Logical entity fields coexist with physical key identifiers. Reverse classification loses primary/foreign entities; Microsoft skips both relationships. |
| Expression-match repair overclassifies | CONVERTER_DEFECT in evaluated PR315 approach | A primary entity and a count measure both use `order_id`; expression matching promotes both. Three focused tests fail before the local fix and pass after it. |
| Complex aggregation fallback | CONVERTER_DEFECT | `SUM(CASE...)` becomes an MSI expression wrapped in a second SUM; the minimized DuckDB query rejects nested aggregates. PR437 fixes this case. |
| Filter token left unresolved | CONVERTER_DEFECT | `item_id__status` remains in generated SQL. PR437 removes the nested SUM but this query still fails, separating two causes. |
| Legacy measure objects removed | EXPECTED_LOSS | The pinned MSI uses direct metric aggregation parameters. Object disappearance alone does not prove different numerical semantics. |
| Unexercised null-fill/offset/private/data-type behavior | UNKNOWN | Absence of a populated source construct is not positive preservation evidence. Existing null/zero query cases retain their independent numerical checks. |

The inventory compares individual definitions and recursively resolved ratio inputs. It records raw representation
changes and does not normalize filters or guessed dataset ownership into apparent equivalence. The original bounded
model/metric checks remain the PASS gate. Attribute classifications and final case statuses are separate.

The full repaired fixture still fails `completed_revenue`, `completed_revenue_share`, and `zero_share`.
The first two reach SQL with unresolved filter identifiers and repeated aggregation. The third cannot resolve the
required product path from the incorrectly assigned numerator model. These are execution failures, **not measured
numerical mismatches**. The six numerical mismatches in the original controls remain deliberately injected faults.

Microsoft emits four BLANK placeholders before and after the key-only repair. Order count has an unresolved
incoming field reference; the filtered aggregate and ratio expressions exceed the converter's translation subset.
The fix preserves two relationships but does not repair metric field references. The authored DAX probe preserves
two expressions, the measure reference, format/display/visibility metadata, the calculated host, and a relationship.
All Power BI runtime outcomes remain NOT_EXECUTED.

Evidence: [sealed report](../labs/semantic_interop/results/task02/verified-003/report.json),
[generated tables](../labs/semantic_interop/docs/DIFFERENTIAL_EVALUATION.md),
[minimal fixture specifications](../labs/semantic_interop/fixtures/regressions/cases.json),
[round-trip diagnosis](../labs/semantic_interop/docs/ROUND_TRIP_DIAGNOSIS.md).

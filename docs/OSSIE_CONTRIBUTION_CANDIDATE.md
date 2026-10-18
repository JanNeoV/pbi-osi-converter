# Ossie contribution candidate: emit keys in the exported field namespace

**Ready for human technical review; not submitted.** The candidate changes two assignments in the dbt exporter.
Primary/unique keys and relationship endpoints now use entity names, matching the fields it already exports.
Physical expressions remain on those fields. The reverse converter and Ossie specification are unchanged.

With entity `order` expressed as `order_id`, the old document exports a field named `order` but a primary key
named `order_id`. The reverse converter checks field names and loses the primary entity. Microsoft independently
rejects analogous relationship endpoints. The proposed document names `order` consistently in its key.

An important counterexample distinguishes this candidate from [PR315](https://github.com/apache/ossie/pull/315):
if an ordinary measure also uses `order_id`, matching field expressions on import promotes that measure to an
additional primary entity. This is demonstrated in `test_reverse_does_not_promote_a_measure_with_the_same_physical_expression`.
The forward-side repair preserves one entity without promoting the measure and also fixes downstream relationship endpoints.

## Reproduction and patch

- [Patch](../labs/semantic_interop/results/task02/upstream-repair-v2/field-name-keys.patch), based on `ef91cb61092c60f4b918016a81cfdbe483e113bc`.
- [Provenance](../labs/semantic_interop/results/task02/upstream-repair-v2/provenance.json). Version 2 only formats the new test; the original patch and its numerical evidence remain intact.
- [Before](../labs/semantic_interop/results/task02/upstream-repair/before.txt), [PR315 comparison](../labs/semantic_interop/results/task02/upstream-repair/pr315.txt), [after](../labs/semantic_interop/results/task02/upstream-repair/after.txt), [full suite](../labs/semantic_interop/results/task02/upstream-repair/suite-after.txt).

```text
git checkout --detach ef91cb61092c60f4b918016a81cfdbe483e113bc
git apply --check /path/to/field-name-keys.patch
git apply /path/to/field-name-keys.patch
# In converters/dbt, with the isolated environment:
python -m pytest -q tests
```

The three new tests fail before and pass after. The final suite reports **179 passing tests and five snapshots**.
The formatted patch repeats that result and passes Ruff lint, new-test formatting, and Git whitespace checks.
Five initial failures in the broader suite were reviewed: four existing assertions and one snapshot encoded physical
key names. Their updated expectations are included in the patch; they were not silently bypassed.

The research experiment then executes 11 oracle-matching queries after explicit spine/default reconstruction.
Both Microsoft relationships are emitted. The four Microsoft placeholders and filtered-revenue failures remain.
Neither the patch alone nor this experiment establishes a lossless round trip.

## Maintainer review considerations

This is an observable output change for aliased entities. Consumers relying on physical key names need review.
The approach aligns with the field-name interpretation discussed in [PR486](https://github.com/apache/ossie/pull/486),
which was still open at review time. The contribution should be coordinated with PR315 and the name-resolution discussion.
It does not broaden support for composite keys, resolve arbitrary metric identifiers, infer default time dimensions,
or repair complex aggregates. PR437 already covers the aggregate case and was independently verified.

All new upstream test code retains ASF headers. No dependency or schema changes are proposed. The patch is AI-assisted;
a human contributor must review behavior, attribution, tests and the required disclosure before submission. Nothing was pushed,
published, posted, or submitted.

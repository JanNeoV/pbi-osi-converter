# Power BI metadata patch result

Applied:
- Event Context Rate: formatString set to 0.0%
- Event Context Rate: displayFolder set to 03 Rates
- Record Integrity Rate: formatString set to 0.0%
- Record Integrity Rate: displayFolder set to 03 Rates
- Individual Profile Rate: formatString set to 0.0%
- Individual Profile Rate: displayFolder set to 03 Rates
- Model Residual Rate: formatString set to 0.0%
- Model Residual Rate: displayFolder set to 03 Rates
- Individual Hard Flag Rate: formatString set to 0.0%
- Individual Hard Flag Rate: displayFolder set to 03 Rates

Skipped:
- fct_result.distance_id -> dim_distance.distance_id
  Reason: structural changes are outside the safe patch scope

Preservation checks:
- table, column, and measure names and counts unchanged
- DAX expressions unchanged
- lineage tags unchanged
- relationships unchanged
- partitions and Power Query expressions unchanged
- source-column mappings unchanged
- column data types and formats unchanged
- only approved metadata fields changed
- complete definition file set preserved
- source definition folder unchanged

Compatibility before:
- metadata drift: 5
- structural drift: 1

Compatibility after:
- metadata drift: 0
- structural drift: 1

Output: `semantic_poc/output/patched_powerbi_definition`
Patched extraction: `semantic_poc/output/patched_powerbi_semantics.json`
Patched compatibility: `semantic_poc/output/patched_semantic_compatibility.md`
Status: success

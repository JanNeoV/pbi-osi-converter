# Semantic Compatibility Report

Generated file. Do not edit manually.
Canonical source: `models/semantic/triathlon_semantic.yml` via `target/semantic_manifest.json`.

| Metric | Canonical dbt definition | Actual Power BI implementation | Generated Snowflake implementation | Status |
| --- | --- | --- | --- | --- |
| `valid_sbr_finishers` | `sum_boolean(is_valid_sbr_finisher = 1)` | `CALCULATE( COUNTROWS(fct_result), fct_result[is_valid_sbr_finisher] = TRUE() )` | generated | `MATCH` |
| `event_context_rows` | `sum_boolean(event_context_flag = 1)` | `CALCULATE( COUNTROWS(fct_result), fct_result[event_context_flag] = TRUE() )` | generated | `MATCH` |
| `event_context_rate` | `event_context_rows / valid_sbr_finishers` | `DIVIDE( [Event Context Rows], [Valid SBR Finishers] )` | generated | `MATCH` |
| `record_integrity_rate` | `record_integrity_rows / valid_sbr_finishers` | `DIVIDE( [Record Integrity Rows], [Valid SBR Finishers] )` | generated | `MATCH` |
| `individual_profile_rate` | `individual_profile_rows / valid_sbr_finishers` | `DIVIDE( [Individual Profile Rows], [Valid SBR Finishers] )` | generated | `MATCH` |
| `model_residual_rate` | `model_residual_rows / valid_sbr_finishers` | `DIVIDE( [Model Residual Rows], [Valid SBR Finishers] )` | generated | `MATCH` |
| `individual_hard_flag_rate` | `individual_hard_flag_rows / valid_sbr_finishers` | `DIVIDE( [Individual Hard Flag Rows], [Valid SBR Finishers] )` | generated | `MATCH` |

## Power BI metadata drift

- None.

## Power BI definition drift

- None.

## Relationship drift

- fct_result.distance_id -> dim_distance.distance_id is missing in Power BI.

## Unsupported cross-platform translation

- None.

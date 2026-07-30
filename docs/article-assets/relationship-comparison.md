# Captured relationship comparison

This note compares the relationship endpoints in the captured Power BI TMDL
with the captured Snowflake semantic-view YAML. It is intentionally limited to
the abstract relationship topology.

| Many-side endpoint | One-side endpoint | Snowflake relationship |
| --- | --- | --- |
| `DIM_DIVSION.AGE_GROUP_ID` | `DIM_AGE_GROUP.AGE_GROUP_ID` | `many_to_one` |
| `DIM_EVENT.COUNTRY_ID` | `DIM_COUNTRY.COUNTRY_ID` | `many_to_one` |
| `FCT_RESULT.DISTANCE_ID` | `DIM_DISTANCE.DISTANCE_ID` | `many_to_one` |
| `FCT_RESULT.DIVISION_ID` | `DIM_DIVSION.DIVISION_ID` | `many_to_one` |
| `FCT_RESULT.EVENT_ID` | `DIM_EVENT.EVENT_ID` | `many_to_one` |
| `FCT_RESULT.GENDER_ID` | `DIM_GENDER.GENDER_ID` | `many_to_one` |
| `FCT_SPLIT.RESULT_ID` | `FCT_RESULT.RESULT_ID` | `many_to_one` |

All seven endpoint pairs are present in both representations. This preserves
the two normalized dimension chains and the result-to-split path:
`DIM_EVENT` filters `FCT_SPLIT` through `FCT_RESULT`; neither representation
contains a direct event-to-split relationship.

Permitted conclusion: the captured Snowflake conversion represented the Power
BI relationship topology fairly well.

Boundary: matching endpoints do not prove that measures, DAX filter context,
business populations, metadata, or runtime results are equivalent. Those
questions are covered by the separate captured conversion audit.

Evidence:

- Power BI: `pbi/pbi_trial.SemanticModel/definition/relationships.tmdl`
- Portable Power BI fixture:
  `semantic_poc/benchmark/pbi_trial_v2/fixtures/pbi_trial.SemanticModel/definition/relationships.tmdl`
- Snowflake: `pbit/snowflake_semantic_view/pbi_trial.yaml`
- Machine findings:
  `semantic_poc/benchmark/pbi_trial_v2/conversion-findings.json`

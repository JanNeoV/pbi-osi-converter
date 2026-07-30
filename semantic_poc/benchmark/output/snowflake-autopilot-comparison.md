# Snowflake Autopilot comparison

No live Autopilot operation was performed.

Fixture B uses a clearly labelled `SYNTHETIC_TEST_FIXTURE` to exercise normalization and failure detection.

| Finding | Severity | Category | Source |
| --- | --- | --- | --- |
| `fnd_53e19af120db604e` | `WARNING` | `GENERATED_DESCRIPTION_UNVERIFIED` | Autopilot dimension Bad Percent |
| `fnd_1f14ae9f7f8e13b1` | `WARNING` | `AGGREGATION_MISMATCH` | Autopilot metric sum_of_success_rate |
| `fnd_5ab4566e744842fe` | `WARNING` | `GENERATED_DESCRIPTION_UNVERIFIED` | Autopilot metric sum_of_success_rate |
| `fnd_ab2e701fdd17d6bd` | `BLOCKING` | `SEMANTIC_ROLE_MISMATCH` | Autopilot dimension Bad Percent |
| `fnd_254ac9fd7b0565a5` | `BLOCKING` | `SEMANTIC_ROLE_MISMATCH` | Autopilot fact numeric_id |
| `fnd_024aaaae65a8a394` | `BLOCKING` | `TYPE_MISMATCH` | Autopilot fact Total Measure |
| `fnd_d230c8efd5ff5299` | `BLOCKING` | `OMITTED_OBJECT` | Measures[Bad Percent] |
| `fnd_a903d541229b54e5` | `BLOCKING` | `OMITTED_OBJECT` | Measures[Rows] |
| `fnd_b2c14cf131a75c63` | `BLOCKING` | `OMITTED_OBJECT` | Measures[Valid Rows] |

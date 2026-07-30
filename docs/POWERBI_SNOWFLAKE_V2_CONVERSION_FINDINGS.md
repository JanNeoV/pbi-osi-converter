# Power BI to Snowflake Conversion Audit Findings

Audit ID: `audit_bef38d48cf0c05ab`
Audit kind: `RECONCILE_TARGET_DRIFT`
Audit engine: `1.0.0`

## Authority and evidence

`models/semantic/triathlon_semantic.yml` is the only production semantic authority. The Power BI model, the benchmark oracle, and the Snowflake export are read-only evidence; this audit creates no change request and performs no application or deployment.

- Canonical contract SHA-256: `12b0bae86103284b68254220bafb3cc14ba428cf9ac446ac29a32b6876e7d1c4`
- Power BI TMDL tree SHA-256: `a9042f554c8c500fdce0f75885322affdda7550f3b2c559c3ddc45b4699581e1`
- Power BI semantic inventory SHA-256: `3ef53ead9750bad8fbd103609d321a569ebe7f3684ac3ec6c001a17776cc0ddd`
- Power BI structural SHA-256: `2a84093d61d2caf24dc96f7cf49a08b86c5e86e3052fb626b881076c415b4d67`
- Snowflake YAML SHA-256: `9957822850a690cccc3eb5b3108f8cefc26a36461b85996f5e98ff3d1744e751`
- Snowflake structural SHA-256: `bfa4a1192fc55a4f18a1a7850ceca705f632791b2e9d305c06a80a23f426378c`
- Benchmark oracle SHA-256: `93c060de6ab00068682e8bf8a60713d4a1e97880511c36a4304d18917200d42c`
- Behavioral baseline SHA-256: `98405395d9724b64196ffed7b5af5c43c5d0cc6b6c8e67febaa87e4a478f4400`
- Snowflake diagnostics SHA-256: `NOT_AVAILABLE`
- Runtime result evidence SHA-256: `NOT_AVAILABLE`

## Executive verdict

**SNOWFLAKE_DID_NOT_PROVE_COMPLETE_OR_CORRECT_CONVERSION.** Snowflake emitted 21 of 46 measures and omitted 25. The audit confirms 4 mistranslations and identifies 4 additional potential semantic mismatches that need differential evidence.

Of 6 intentional defects, 0 are proven caught. Silent omission is `NOT_PROVEN`; cautionary prose on an active metric is not a blocking diagnostic.

The static scope is aligned with Snowflake's documented support categories for [Power BI ingestion](https://docs.snowflake.com/en/user-guide/views-semantic/power-bi-ingestion). Runtime queries use the documented [semantic-view query form](https://docs.snowflake.com/en/user-guide/views-semantic/querying).

## All measures

| Case | Power BI measure | Canonical metric | Snowflake metric | Fidelity | Behavior | Detection | Handling | Automation | Finding codes |
|---|---|---|---|---|---|---|---|---|---|
| `pbiv2_001` | `Result Rows` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_002` | `Split Rows` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_003` | `Split Time Seconds` | unresolved benchmark evidence | `FCT_SPLIT.SPLIT_TIME_SECONDS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_004` | `Results With Splits` | unresolved benchmark evidence | `FCT_SPLIT.RESULTS_WITH_SPLITS` | `POTENTIALLY_INCORRECT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `DAX_BLANK_SQL_NULL_RISK` |
| `pbiv2_005` | `# Events` | unresolved benchmark evidence | `FCT_RESULT.EVENTS` | `POTENTIALLY_INCORRECT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `DAX_BLANK_SQL_NULL_RISK` |
| `pbiv2_006` | `Swim Time Seconds` | unresolved benchmark evidence | `FCT_RESULT.SWIM_TIME_SECONDS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_007` | `Bike Time Seconds` | unresolved benchmark evidence | `FCT_RESULT.BIKE_TIME_SECONDS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_008` | `Run Time Seconds` | unresolved benchmark evidence | `FCT_RESULT.RUN_TIME_SECONDS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_009` | `Total Transition Seconds` | unresolved benchmark evidence | `FCT_RESULT.TOTAL_TRANSITION_SECONDS` | `POTENTIALLY_INCORRECT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `DAX_BLANK_SQL_NULL_ARITHMETIC_RISK` |
| `pbiv2_010` | `Total Recorded Seconds` | unresolved benchmark evidence | `FCT_RESULT.TOTAL_RECORDED_SECONDS` | `POTENTIALLY_INCORRECT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `DAX_BLANK_SQL_NULL_ARITHMETIC_RISK` |
| `pbiv2_011` | `Finisher Rows` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_012` | `Valid SBR Finishers` | `valid_sbr_finishers` | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_013` | `Review Rows` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_014` | `Reviewed Valid SBR Finishers` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_015` | `Pro Finisher Rows` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_016` | `Finish Rate` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_017` | `Valid SBR Rate` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_018` | `Review Rate Among Valid SBR` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_019` | `Split Coverage Rate` | unresolved benchmark evidence | `FCT_SPLIT.SPLIT_COVERAGE_RATE` | `CONFIRMED_INCORRECT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `CHANGED` | `MANUAL_REVIEW_REQUIRED` | `WRONG_DENOMINATOR_AND_GRAIN` |
| `pbiv2_020` | `Average Bike Seconds` | unresolved benchmark evidence | `FCT_RESULT.AVERAGE_BIKE_SECONDS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_021` | `Min Bike Seconds` | unresolved benchmark evidence | `FCT_RESULT.MIN_BIKE_SECONDS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_022` | `Max Bike Seconds` | unresolved benchmark evidence | `FCT_RESULT.MAX_BIKE_SECONDS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_023` | `Median Complete SBR Seconds` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_024` | `P90 Complete SBR Seconds` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_025` | `Complete SBR Population StdDev Seconds` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_026` | `Run Time Hours` | unresolved benchmark evidence | `FCT_RESULT.RUN_TIME_HOURS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_027` | `Bike Time Hours` | unresolved benchmark evidence | `FCT_RESULT.BIKE_TIME_HOURS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_028` | `Swim Time Hours` | unresolved benchmark evidence | `FCT_RESULT.SWIM_TIME_HOURS` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `EMITTED` | `MANUAL_REVIEW_REQUIRED` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_029` | `Nominal Bike KM Across Timed Results` | unresolved benchmark evidence | `DIM_DISTANCE.NOMINAL_BIKE_KM_ACROSS_TIMED_RESULTS` | `CONFIRMED_INCORRECT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `CHANGED` | `MANUAL_REVIEW_REQUIRED` | `FILTER_AND_GRAIN_LOSS` |
| `pbiv2_030` | `Weighted Bike Speed KM/H` | unresolved benchmark evidence | `WEIGHTED_BIKE_SPEED_KM_H` | `CONFIRMED_INCORRECT` | `NOT_AVAILABLE` | `NOT_PROVEN` | `CHANGED` | `MANUAL_REVIEW_REQUIRED` | `POPULATION_AND_GRAIN_DRIFT` |
| `pbiv2_031` | `Valid SBR Share Across Visible Distances` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_032` | `Distance Rank by Valid SBR` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_033` | `Event-Weighted Average Valid SBR Rate` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_034` | `Valid SBR % of Parent Geography` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_035` | `Selected Leg Time Seconds` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_036` | `Top 3 Events Valid SBR Share` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_037` | `Complete Five-Split Results` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_038` | `Complete Split Coverage Rate` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_039` | `Split Event Mismatch Rows` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_040` | `All-Leg Split vs Result Delta Seconds` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `MANUAL_REVIEW_REQUIRED` | `TARGET_OMISSION` |
| `pbiv2_041` | `NC - Bike Time Hours Divisor 60` | unresolved benchmark evidence | `FCT_RESULT.NC_BIKE_TIME_HOURS_DIVISOR_60` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `PROVEN_NOT_CAUGHT` | `EMITTED_WITH_CAUTION` | `FLAG_SOURCE_DEFECT` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_042` | `NC - Event ID Total` | unresolved benchmark evidence | `FCT_RESULT.NC_EVENT_ID_TOTAL` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `PROVEN_NOT_CAUGHT` | `EMITTED_WITH_CAUTION` | `FLAG_SOURCE_DEFECT` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_043` | `NC - Overall Relative Total` | unresolved benchmark evidence | `FCT_RESULT.NC_OVERALL_RELATIVE_TOTAL` | `STRUCTURALLY_EQUIVALENT` | `NOT_AVAILABLE` | `PROVEN_NOT_CAUGHT` | `EMITTED_WITH_CAUTION` | `FLAG_SOURCE_DEFECT` | `ALLOWLISTED_STRUCTURAL_EQUIVALENCE` |
| `pbiv2_044` | `NC - Review Rate Wrong Population` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `FLAG_SOURCE_DEFECT` | `TARGET_OMISSION` |
| `pbiv2_045` | `NC - Valid SBR Rate Decimal Format` | unresolved benchmark evidence | — | `OMITTED` | `NOT_AVAILABLE` | `NOT_PROVEN` | `OMITTED` | `FLAG_SOURCE_DEFECT` | `TARGET_OMISSION` |
| `pbiv2_046` | `NC - Split-Multiplied Bike Seconds` | unresolved benchmark evidence | `FCT_RESULT.NC_SPLIT_MULTIPLIED_BIKE_SECONDS` | `CONFIRMED_INCORRECT` | `NOT_AVAILABLE` | `PROVEN_NOT_CAUGHT` | `CHANGED` | `FLAG_SOURCE_DEFECT` | `FACT_GRAIN_FANOUT_REMOVED` |

## Negative-control detection scorecard

| Control | Defect | Target handling | Detection | Fidelity |
|---|---|---|---|---|
| `NC - Bike Time Hours Divisor 60` | `UNIT_CONVERSION_MISMATCH` | `EMITTED_WITH_CAUTION` | `PROVEN_NOT_CAUGHT` | `STRUCTURALLY_EQUIVALENT` |
| `NC - Event ID Total` | `IDENTIFIER_AGGREGATION` | `EMITTED_WITH_CAUTION` | `PROVEN_NOT_CAUGHT` | `STRUCTURALLY_EQUIVALENT` |
| `NC - Overall Relative Total` | `NON_ADDITIVE_AGGREGATION` | `EMITTED_WITH_CAUTION` | `PROVEN_NOT_CAUGHT` | `STRUCTURALLY_EQUIVALENT` |
| `NC - Review Rate Wrong Population` | `POPULATION_MISMATCH` | `OMITTED` | `NOT_PROVEN` | `OMITTED` |
| `NC - Valid SBR Rate Decimal Format` | `FORMAT_MISMATCH` | `OMITTED` | `NOT_PROVEN` | `OMITTED` |
| `NC - Split-Multiplied Bike Seconds` | `FACT_GRAIN_FANOUT` | `CHANGED` | `PROVEN_NOT_CAUGHT` | `CONFIRMED_INCORRECT` |

## Confirmed mistranslations

- `Split Coverage Rate` — Snowflake divides distinct split result IDs by split rows; Power BI divides by result-grain Result Rows. Finding: `fnd_763847611e42b68f`.
- `Nominal Bike KM Across Timed Results` — Snowflake sums the distance dimension and loses both the positive-bike-time population and one-distance-per-result iteration. Finding: `fnd_35b5e172794e4148`.
- `Weighted Bike Speed KM/H` — Snowflake does not preserve the shared positive-bike-time population and aggregates across fact and dimension grains. Finding: `fnd_aae8ccbcaaa599e3`.
- `NC - Split-Multiplied Bike Seconds` — Snowflake emits a result-grain SUM and does not preserve the source split-grain fanout calculation. Finding: `fnd_994490b52ec04d9a`.

## Potential semantic mismatches

- `Results With Splits` — DAX DISTINCTCOUNT and SQL COUNT(DISTINCT ...) can differ for BLANK/NULL and empty contexts. Behavioral status: `NOT_AVAILABLE`.
- `# Events` — DAX DISTINCTCOUNT and SQL COUNT(DISTINCT ...) can differ for BLANK/NULL and empty contexts. Behavioral status: `NOT_AVAILABLE`.
- `Total Transition Seconds` — DAX blank arithmetic can coerce a missing component to zero while SQL addition propagates NULL. Behavioral status: `NOT_AVAILABLE`.
- `Total Recorded Seconds` — DAX blank arithmetic can coerce a missing component to zero while SQL addition propagates NULL. Behavioral status: `NOT_AVAILABLE`.

## Omissions

25 measures were omitted without defect-specific rejection evidence:

`Result Rows`, `Split Rows`, `Finisher Rows`, `Valid SBR Finishers`, `Review Rows`, `Reviewed Valid SBR Finishers`, `Pro Finisher Rows`, `Finish Rate`, `Valid SBR Rate`, `Review Rate Among Valid SBR`, `Median Complete SBR Seconds`, `P90 Complete SBR Seconds`, `Complete SBR Population StdDev Seconds`, `Valid SBR Share Across Visible Distances`, `Distance Rank by Valid SBR`, `Event-Weighted Average Valid SBR Rate`, `Valid SBR % of Parent Geography`, `Selected Leg Time Seconds`, `Top 3 Events Valid SBR Share`, `Complete Five-Split Results`, `Complete Split Coverage Rate`, `Split Event Mismatch Rows`, `All-Leg Split vs Result Delta Seconds`, `NC - Review Rate Wrong Population`, `NC - Valid SBR Rate Decimal Format`.

## Relationships, grain, and metadata

- Power BI inventory: 9 tables, 61 columns, 46 measures, and 7 relationships.
- Snowflake inventory: 8 logical tables, 43 dimensions, 1 time dimension, 16 facts, and 21 metrics.
- Relationship endpoints matched for 7 of 7 Power BI relationships.
- Default active, single-direction relationships are compatibility-checked. Inactive, bidirectional, cardinality-drifted, missing, or duplicate relationships are blockers; explicit relationship-property provenance remains unrepresented.
- Reviewed model-structure status: `MATCH`.
- Metadata-loss counts: `{"BLOCKING_SAFETY_LABEL_NOT_PRESERVED": 4, "DESCRIPTION_MISMATCH": 21, "DISPLAY_FOLDER_NOT_PRESERVED": 21, "EXPERIMENTAL_PROVENANCE_NOT_PRESERVED": 17, "FORMAT_NOT_PRESERVED": 21, "LINEAGE_NOT_PRESERVED": 21, "UNIT_METADATA_NOT_PRESERVED": 21}`.
- Target descriptions replace the benchmark provenance and weaken negative-control safety labels. Formats, display folders, units, and source lineage are not represented.

## Runtime evidence

Runtime evidence is `NOT_AVAILABLE`. A pass is accepted only when every required slice is supplied with current input hashes and complete Power BI and Snowflake grouped exports have identical coordinate sets. Integers compare exactly; decimals use absolute and relative tolerance `1e-9`. Missing metrics, missing rows, missing slices, and unavailable exports remain `NOT_AVAILABLE` or fail comparison.

Required slices: `OVERALL`, `DISTANCE`, `GENDER`, `EVENT`, `COUNTRY`, `DIVISION`, `AGE_GROUP`, `LEG`.

## Deterministic conversion recipes

- `AUTO_CONVERT`: only after exact canonical resolution, compile the proof-backed typed pattern into a canonical-first proposal or candidate YAML; do not approve or deploy it.
- `FLAG_SOURCE_DEFECT`: preserve the oracle defect evidence, stop automatic conversion, and route the named correction through canonical review.
- `MANUAL_REVIEW_REQUIRED`: stop for ambiguity, context transition, relationship paths, fanout, unsupported iterators/ranking, metadata-only defects, or unresolved dependencies.
- Never synthesize arbitrary final DAX or Snowflake SQL. Target definitions must come from deterministic typed compilers.

## Reproduction

```text
semantic-agent audit-powerbi-snowflake --model-dir "semantic_poc/benchmark/pbi_trial_v2/fixtures/pbi_trial.SemanticModel" --snowflake-yaml "pbit/snowflake_semantic_view/pbi_trial.yaml" --benchmark-spec "semantic_poc/benchmark/pbi_trial_v2/measure-cases.yml" --output-dir <controlled-directory> --check
python semantic_poc/run_pbi_trial_v2_audit.py --check
```

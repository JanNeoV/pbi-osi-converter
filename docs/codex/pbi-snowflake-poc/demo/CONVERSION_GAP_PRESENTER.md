# Captured Power BI-to-Snowflake conversion gap

## 1. Reproduce the evidence

[PROVEN] This walkthrough is scoped only to the captured `pbi_trial` v2 inputs and shows that this captured output is unsafe to accept blindly.

```powershell
python semantic_poc/run_pbi_trial_v2_audit.py --check --json
```

## 2. Coverage

[OBSERVED] The source inventory contains 46 Power BI measures; the captured Snowflake YAML emits 21 mapped metrics and omits 25 measures.

## 3. Three representative findings

[OBSERVED] `Result Rows` is a trivial omission with no emitted target metric (finding `fnd_023236b34876f0e3`).

[PROVEN] `Split Coverage Rate` changes denominator and grain by dividing distinct split result IDs by split rows instead of using result-grain `Result Rows` (finding `fnd_763847611e42b68f`).

[PROVEN] `NC - Bike Time Hours Divisor 60` is an intentional unit-conversion defect that remained active with only non-blocking caution (finding `fnd_a02606d48dc92574`).

## 4. What the static evidence proves

[PROVEN] Four measures are confirmed mistranslations: `Split Coverage Rate`, `Nominal Bike KM Across Timed Results`, `Weighted Bike Speed KM/H`, and `NC - Split-Multiplied Bike Seconds`.

[PROVEN] Zero of six intentional negative controls were proven caught; four were proven not caught.

[NOT_PROVEN] The two silently omitted intentional controls do not establish detection and remain `NOT_PROVEN`.

## 5. Model structure and runtime boundary

[OBSERVED] All seven Power BI relationship endpoints matched the captured target representation, and the reviewed model structure has no blocker.

[NOT_PROVEN] Runtime equivalence and runtime mismatch are both unproven because sanitized comparable result exports were not supplied.

[NOT_PROVEN] This captured case does not prove that Snowflake conversion always fails.

## 6. Follow-up POC

[OBSERVED] The next POC stage is deterministic conversion plus governed review and incremental reconciliation—not an AI model writing executable SQL.

# Governed review session

- Decision ID: `review_decision_guided_sync_demo_v1`
- Preview ID: `prv_db9d5587a40f27fc3c2be7dc`
- Finding ID: `fnd_96fc798625d42d3a8129e23f`
- Review class: `FORMULA_REVIEW_REQUIRED`
- Result status: `REVIEW_RECORDED`
- Canonical metric: `UNRESOLVED`
- Source object: `Measures[Hours]`
- Affected targets: `POWER_BI, SNOWFLAKE`
- Approval state: `NOT_REQUESTED`
- Application state: `NOT_REQUESTED`
- Deployment authorized: `false`

## Question

Review the structured validation finding.

## Selected answer

`CONFIRM_REGISTERED_REVIEW_RULE` with `{"rule_id": "review_unit_conversion_seconds_to_hours_v1", "semantic_signature_sha256": "7be299ab2c4c0765ed51f905c4e95e709f3f467532934eac65682faa6b0c2c7f", "sha256": "af996d6a1d96e5f8a75b0b34baf567f0b47788523d2722057d877eef9e451e4a"}`

## Rationale

The exact registered unit rule remains applicable.

## Evidence

- `evidence_queue` — `PREVIEW_ARTIFACT` — `.tmp/guided-sync-demo/work/hours-preview/validation-queue.json`
- `evidence_rule` — `REVIEW_RULE` — `semantic_poc/review_memory/accepted/unit_conversion_seconds_to_hours.yml`

## Unresolved validation

- The selected finding was recorded as resolved; no operation was applied.


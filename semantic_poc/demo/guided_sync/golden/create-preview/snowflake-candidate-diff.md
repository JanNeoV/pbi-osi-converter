# Snowflake candidate semantic diff

- Target mode: `create`
- Additions: `43`
- Removals: `0`
- Changes: `0`

## Additions

- `dimension:distances.distance`
- `dimension:distances.distance_id`
- `dimension:divisions.division`
- `dimension:divisions.division_id`
- `dimension:divisions.is_pro`
- `dimension:events.event_id`
- `dimension:events.race_name`
- `dimension:genders.gender`
- `dimension:genders.gender_id`
- `dimension:results.distance_id`
- `dimension:results.division_id`
- `dimension:results.event_id`
- `dimension:results.gender_id`
- `dimension:results.result_id`
- `fact:results.any_review_flag`
- `fact:results.event_context_flag`
- `fact:results.individual_hard_flag`
- `fact:results.individual_profile_flag`
- `fact:results.is_valid_sbr_finisher`
- `fact:results.model_residual_flag`
- `fact:results.record_integrity_flag`
- `logical_table:distances`
- `logical_table:divisions`
- `logical_table:events`
- `logical_table:genders`
- `logical_table:results`
- `metric:event_context_rate`
- `metric:individual_hard_flag_rate`
- `metric:individual_profile_rate`
- `metric:model_residual_rate`
- `metric:record_integrity_rate`
- `metric:results.event_context_rows`
- `metric:results.individual_hard_flag_rows`
- `metric:results.individual_profile_rows`
- `metric:results.model_residual_rows`
- `metric:results.record_integrity_rows`
- `metric:results.valid_sbr_finishers`
- `relationship:results_to_distances`
- `relationship:results_to_divisions`
- `relationship:results_to_events`
- `relationship:results_to_genders`
- `semantic_view:TRIATHLON_ANALYTICS`
- `time_dimension:events.event_year`

## Removals

- None

## Changes

- None

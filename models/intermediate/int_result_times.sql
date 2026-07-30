{{ config(materialized='table', schema='intermediate') }}

with staged as (
    select *
    from {{ ref('stg_results') }}
),

parsed as (
    select
        *,
        {{ parse_triathlon_time_to_seconds('overall_time_raw') }} as overall_seconds,
        {{ parse_triathlon_time_to_seconds('swim_time_raw') }} as swim_seconds,
        {{ parse_triathlon_time_to_seconds('bike_time_raw') }} as bike_seconds,
        {{ parse_triathlon_time_to_seconds('run_time_raw') }} as run_seconds,
        {{ parse_triathlon_time_to_seconds('t1_time_raw') }} as t1_seconds,
        {{ parse_triathlon_time_to_seconds('t2_time_raw') }} as t2_seconds
    from staged
)

select
    *,
    case
        when swim_seconds is not null and bike_seconds is not null and run_seconds is not null
        then swim_seconds + bike_seconds + run_seconds
    end as sbr_seconds,
    case
        when t1_seconds is not null and t2_seconds is not null
        then t1_seconds + t2_seconds
    end as transition_seconds,
    case
        when swim_seconds is not null
            and bike_seconds is not null
            and run_seconds is not null
            and t1_seconds is not null
            and t2_seconds is not null
        then swim_seconds + bike_seconds + run_seconds + t1_seconds + t2_seconds
    end as parts_seconds,
    case
        when overall_seconds is not null
            and swim_seconds is not null
            and bike_seconds is not null
            and run_seconds is not null
            and t1_seconds is not null
            and t2_seconds is not null
        then overall_seconds - (swim_seconds + bike_seconds + run_seconds + t1_seconds + t2_seconds)
    end as reconciliation_delta_seconds,
    case
        when overall_seconds is not null
            and swim_seconds is not null
            and bike_seconds is not null
            and run_seconds is not null
            and t1_seconds is not null
            and t2_seconds is not null
        then abs(overall_seconds - (swim_seconds + bike_seconds + run_seconds + t1_seconds + t2_seconds))
    end as abs_reconciliation_delta_seconds,
    cast(case when overall_seconds is not null then 1 else 0 end as bit) as has_valid_overall_time,
    cast(case when swim_seconds is not null then 1 else 0 end as bit) as has_valid_swim,
    cast(case when bike_seconds is not null then 1 else 0 end as bit) as has_valid_bike,
    cast(case when run_seconds is not null then 1 else 0 end as bit) as has_valid_run,
    cast(case when t1_seconds is not null then 1 else 0 end as bit) as has_valid_t1,
    cast(case when t2_seconds is not null then 1 else 0 end as bit) as has_valid_t2,
    cast(case when swim_seconds is not null and bike_seconds is not null and run_seconds is not null then 1 else 0 end as bit) as has_complete_sbr,
    cast(case when gender in ('Male', 'Female') then 1 else 0 end as bit) as has_valid_gender,
    cast(case when finish_status = 'FIN' then 1 else 0 end as bit) as is_finisher,
    cast(case
        when finish_status = 'FIN'
            and gender in ('Male', 'Female')
            and swim_seconds is not null
            and bike_seconds is not null
            and run_seconds is not null
        then 1 else 0
    end as bit) as is_valid_sbr_finisher
from parsed

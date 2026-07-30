-- Sanitized Snowflake semantic-view differential query pack.
-- Replace <database>.<schema>.<semantic_view> locally; never commit account identifiers or raw results.
-- Query form: SEMANTIC_VIEW(... DIMENSIONS ... METRICS ...).
-- Runtime evidence must use the exact qualified DIMENSIONS names as coordinate keys.
-- EVENT preserves five baseline coordinates; DIVISION preserves DIVISION and IS_PRO.
-- COUNTRY emits detail, region, continent, and grand-total grouping queries; omit non-grouped coordinate keys when merging exports.
-- Audit: audit_bef38d48cf0c05ab

-- pbiv2_001 | Result Rows | OMITTED: no runnable target metric
-- pbiv2_002 | Split Rows | OMITTED: no runnable target metric
-- pbiv2_003 | Split Time Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_SPLIT.SPLIT_TIME_SECONDS
);

-- pbiv2_003 | Split Time Seconds | LEG | grouping 1/1
-- Evidence coordinate keys: {FCT_SPLIT.LEG}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS FCT_SPLIT.LEG
  METRICS FCT_SPLIT.SPLIT_TIME_SECONDS
);

-- pbiv2_004 | Results With Splits | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_SPLIT.RESULTS_WITH_SPLITS
);

-- pbiv2_004 | Results With Splits | LEG | grouping 1/1
-- Evidence coordinate keys: {FCT_SPLIT.LEG}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS FCT_SPLIT.LEG
  METRICS FCT_SPLIT.RESULTS_WITH_SPLITS
);

-- pbiv2_005 | # Events | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.EVENTS
);

-- pbiv2_006 | Swim Time Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.SWIM_TIME_SECONDS
);

-- pbiv2_007 | Bike Time Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.BIKE_TIME_SECONDS
);

-- pbiv2_008 | Run Time Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.RUN_TIME_SECONDS
);

-- pbiv2_009 | Total Transition Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.TOTAL_TRANSITION_SECONDS
);

-- pbiv2_010 | Total Recorded Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.TOTAL_RECORDED_SECONDS
);

-- pbiv2_011 | Finisher Rows | OMITTED: no runnable target metric
-- pbiv2_012 | Valid SBR Finishers | OMITTED: no runnable target metric
-- pbiv2_013 | Review Rows | OMITTED: no runnable target metric
-- pbiv2_014 | Reviewed Valid SBR Finishers | OMITTED: no runnable target metric
-- pbiv2_015 | Pro Finisher Rows | OMITTED: no runnable target metric
-- pbiv2_016 | Finish Rate | OMITTED: no runnable target metric
-- pbiv2_017 | Valid SBR Rate | OMITTED: no runnable target metric
-- pbiv2_018 | Review Rate Among Valid SBR | OMITTED: no runnable target metric
-- pbiv2_019 | Split Coverage Rate | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_SPLIT.SPLIT_COVERAGE_RATE
);

-- pbiv2_020 | Average Bike Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.AVERAGE_BIKE_SECONDS
);

-- pbiv2_020 | Average Bike Seconds | DISTANCE | grouping 1/1
-- Evidence coordinate keys: {DIM_DISTANCE.DISTANCE}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS DIM_DISTANCE.DISTANCE
  METRICS FCT_RESULT.AVERAGE_BIKE_SECONDS
);

-- pbiv2_021 | Min Bike Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.MIN_BIKE_SECONDS
);

-- pbiv2_021 | Min Bike Seconds | DISTANCE | grouping 1/1
-- Evidence coordinate keys: {DIM_DISTANCE.DISTANCE}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS DIM_DISTANCE.DISTANCE
  METRICS FCT_RESULT.MIN_BIKE_SECONDS
);

-- pbiv2_022 | Max Bike Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.MAX_BIKE_SECONDS
);

-- pbiv2_022 | Max Bike Seconds | DISTANCE | grouping 1/1
-- Evidence coordinate keys: {DIM_DISTANCE.DISTANCE}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS DIM_DISTANCE.DISTANCE
  METRICS FCT_RESULT.MAX_BIKE_SECONDS
);

-- pbiv2_023 | Median Complete SBR Seconds | OMITTED: no runnable target metric
-- pbiv2_024 | P90 Complete SBR Seconds | OMITTED: no runnable target metric
-- pbiv2_025 | Complete SBR Population StdDev Seconds | OMITTED: no runnable target metric
-- pbiv2_026 | Run Time Hours | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.RUN_TIME_HOURS
);

-- pbiv2_027 | Bike Time Hours | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.BIKE_TIME_HOURS
);

-- pbiv2_028 | Swim Time Hours | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.SWIM_TIME_HOURS
);

-- pbiv2_029 | Nominal Bike KM Across Timed Results | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS DIM_DISTANCE.NOMINAL_BIKE_KM_ACROSS_TIMED_RESULTS
);

-- pbiv2_029 | Nominal Bike KM Across Timed Results | DISTANCE | grouping 1/1
-- Evidence coordinate keys: {DIM_DISTANCE.DISTANCE}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS DIM_DISTANCE.DISTANCE
  METRICS DIM_DISTANCE.NOMINAL_BIKE_KM_ACROSS_TIMED_RESULTS
);

-- pbiv2_030 | Weighted Bike Speed KM/H | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS WEIGHTED_BIKE_SPEED_KM_H
);

-- pbiv2_030 | Weighted Bike Speed KM/H | DISTANCE | grouping 1/1
-- Evidence coordinate keys: {DIM_DISTANCE.DISTANCE}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS DIM_DISTANCE.DISTANCE
  METRICS WEIGHTED_BIKE_SPEED_KM_H
);

-- pbiv2_030 | Weighted Bike Speed KM/H | AGE_GROUP | grouping 1/1
-- Evidence coordinate keys: {DIM_AGE_GROUP.AGE_GROUP_NAME}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS DIM_AGE_GROUP.AGE_GROUP_NAME
  METRICS WEIGHTED_BIKE_SPEED_KM_H
);

-- pbiv2_031 | Valid SBR Share Across Visible Distances | OMITTED: no runnable target metric
-- pbiv2_032 | Distance Rank by Valid SBR | OMITTED: no runnable target metric
-- pbiv2_033 | Event-Weighted Average Valid SBR Rate | OMITTED: no runnable target metric
-- pbiv2_034 | Valid SBR % of Parent Geography | OMITTED: no runnable target metric
-- pbiv2_035 | Selected Leg Time Seconds | OMITTED: no runnable target metric
-- pbiv2_036 | Top 3 Events Valid SBR Share | OMITTED: no runnable target metric
-- pbiv2_037 | Complete Five-Split Results | OMITTED: no runnable target metric
-- pbiv2_038 | Complete Split Coverage Rate | OMITTED: no runnable target metric
-- pbiv2_039 | Split Event Mismatch Rows | OMITTED: no runnable target metric
-- pbiv2_040 | All-Leg Split vs Result Delta Seconds | OMITTED: no runnable target metric
-- pbiv2_041 | NC - Bike Time Hours Divisor 60 | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.NC_BIKE_TIME_HOURS_DIVISOR_60
);

-- pbiv2_042 | NC - Event ID Total | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.NC_EVENT_ID_TOTAL
);

-- pbiv2_043 | NC - Overall Relative Total | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.NC_OVERALL_RELATIVE_TOTAL
);

-- pbiv2_044 | NC - Review Rate Wrong Population | OMITTED: no runnable target metric
-- pbiv2_045 | NC - Valid SBR Rate Decimal Format | OMITTED: no runnable target metric
-- pbiv2_046 | NC - Split-Multiplied Bike Seconds | OVERALL | grouping 1/1
-- Evidence coordinate keys: {}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  METRICS FCT_RESULT.NC_SPLIT_MULTIPLIED_BIKE_SECONDS
);

-- pbiv2_046 | NC - Split-Multiplied Bike Seconds | LEG | grouping 1/1
-- Evidence coordinate keys: {FCT_SPLIT.LEG}
SELECT *
FROM SEMANTIC_VIEW(
  <database>.<schema>.<semantic_view>
  DIMENSIONS FCT_SPLIT.LEG
  METRICS FCT_RESULT.NC_SPLIT_MULTIPLIED_BIKE_SECONDS
);

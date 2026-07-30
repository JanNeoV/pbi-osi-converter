from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

from semantic_poc.agent.powerbi_import import (
    ImportMetricCandidate,
    analyze_dax_measure,
    build_import_metric_candidates,
    extract_powerbi_inventory,
)
from semantic_poc.src.models import (
    CANONICAL_SOURCE,
    DBT_SEMANTIC_MANIFEST,
    DBT_SEMANTIC_YAML,
    load_json,
    load_yaml,
)
from semantic_poc.src.semantic_ir import (
    Aggregation,
    MetricPattern,
    PowerBIMapping,
    SnowflakeMapping,
    SupportClassification,
    build_canonical_metric_ir_index,
    build_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PBI_TRIAL_MODEL = (
    REPOSITORY_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "fixtures"
    / "pbi_trial.SemanticModel"
)


def _current_ir():
    manifest = load_json(DBT_SEMANTIC_MANIFEST)
    canonical = load_yaml(DBT_SEMANTIC_YAML)
    return build_metric_ir_index(
        manifest,
        canonical,
        canonical_source=CANONICAL_SOURCE,
    )


def test_direct_average_min_and_max_are_explicit_supported_patterns() -> None:
    expected = {
        "AVERAGE(fact[value])": ("AVERAGE", "AVERAGE"),
        "MIN(fact[value])": ("MIN", "MIN"),
        "MAX(fact[value])": ("MAX", "MAX"),
    }

    for expression, (pattern, kind) in expected.items():
        result = analyze_dax_measure(expression)
        assert result.supported is True
        assert result.pattern == pattern
        assert result.ast == {
            "kind": kind,
            "table": "fact",
            "column": "value",
        }
        assert result.column_dependencies == ("fact[value]",)


def test_addition_patterns_are_narrow_and_preserve_dependencies() -> None:
    sums = analyze_dax_measure(
        "SUM(fact[t1_seconds]) + SUM(fact[t2_seconds])"
    )
    measures = analyze_dax_measure(
        "[Swim Seconds] + [Bike Seconds] + [Run Seconds]",
        known_measure_names=("Swim Seconds", "Bike Seconds", "Run Seconds"),
    )
    cross_table = analyze_dax_measure(
        "SUM(fact_a[value]) + SUM(fact_b[value])"
    )

    assert sums.supported is True
    assert sums.pattern == "SUM_ADDITION"
    assert sums.column_dependencies == (
        "fact[t1_seconds]",
        "fact[t2_seconds]",
    )
    assert measures.supported is True
    assert measures.pattern == "METRIC_ADDITION"
    assert measures.ast == {
        "kind": "METRIC_ADDITION",
        "references": ["Swim Seconds", "Bike Seconds", "Run Seconds"],
    }
    assert measures.measure_dependencies == (
        "Bike Seconds",
        "Run Seconds",
        "Swim Seconds",
    )
    assert cross_table.supported is False
    assert {item.code for item in cross_table.diagnostics} == {
        "DAX_PATTERN_UNSUPPORTED"
    }


def test_single_boolean_keepfilters_is_supported_but_context_expansion_is_not() -> None:
    supported = analyze_dax_measure(
        "CALCULATE(COUNTROWS(fact), KEEPFILTERS(fact[is_valid] = TRUE()))"
    )
    multiple = analyze_dax_measure(
        "CALCULATE("
        "COUNTROWS(fact), "
        "KEEPFILTERS(fact[is_valid] = TRUE()), "
        "KEEPFILTERS(fact[is_reviewed] = TRUE())"
        ")"
    )
    non_boolean = analyze_dax_measure(
        "CALCULATE(COUNTROWS(fact), KEEPFILTERS(fact[state] = 1))"
    )

    assert supported.supported is True
    assert supported.pattern == "FILTERED_COUNT"
    assert supported.filters[0].keep_filters is True
    assert supported.filters[0].to_dict()["keep_filters"] is True
    assert multiple.supported is False
    assert "DAX_FILTER_CONTEXT_MODIFIER" in {
        item.code for item in multiple.diagnostics
    }
    assert non_boolean.supported is False
    assert "DAX_FILTER_CONTEXT_MODIFIER" in {
        item.code for item in non_boolean.diagnostics
    }


def test_positive_scaled_measure_reference_is_supported_and_zero_is_rejected() -> None:
    supported = analyze_dax_measure(
        "DIVIDE([Bike Time Seconds], 3600)",
        known_measure_names=("Bike Time Seconds",),
    )
    rejected = analyze_dax_measure(
        "DIVIDE([Bike Time Seconds], 0)",
        known_measure_names=("Bike Time Seconds",),
    )

    assert supported.supported is True
    assert supported.pattern == "SCALED_METRIC"
    assert supported.ast == {
        "kind": "SCALED_METRIC",
        "reference": "Bike Time Seconds",
        "divisor": "3600",
    }
    assert supported.measure_dependencies == ("Bike Time Seconds",)
    assert rejected.supported is False
    assert {item.code for item in rejected.diagnostics} == {
        "DAX_PATTERN_UNSUPPORTED"
    }


def test_typed_ir_compiles_new_column_aggregate_and_addition_patterns() -> None:
    index = _current_ir()
    base = index["result_rows"]

    aggregate_expectations = {
        MetricPattern.AVERAGE: (
            Aggregation.AVERAGE,
            "AVERAGE(fct_result[SUM_OF_BIKE_SECONDS])",
            "AVG(SUM_OF_BIKE_SECONDS)",
        ),
        MetricPattern.MIN: (
            Aggregation.MIN,
            "MIN(fct_result[SUM_OF_BIKE_SECONDS])",
            "MIN(SUM_OF_BIKE_SECONDS)",
        ),
        MetricPattern.MAX: (
            Aggregation.MAX,
            "MAX(fct_result[SUM_OF_BIKE_SECONDS])",
            "MAX(SUM_OF_BIKE_SECONDS)",
        ),
    }
    for pattern, (aggregation, expected_dax, expected_snowflake) in (
        aggregate_expectations.items()
    ):
        metric = replace(
            base,
            canonical_name=f"test_{pattern.value.casefold()}",
            label=f"Test {pattern.value}",
            pattern=pattern,
            aggregation=aggregation,
            source_field="SUM_OF_BIKE_SECONDS",
            power_bi=PowerBIMapping(
                table="MEASURES_",
                measure=f"Test {pattern.value}",
            ),
            snowflake=SnowflakeMapping(
                logical_table="results",
                metric_name=f"test_{pattern.value.casefold()}",
            ),
        )
        metric_index = {**index, metric.canonical_name: metric}
        assert generate_dax_definition(metric, metric_index).definition == expected_dax
        assert (
            generate_snowflake_definition(metric, metric_index).definition["expr"]
            == expected_snowflake
        )

    sum_addition = replace(
        base,
        canonical_name="transition_seconds",
        label="Transition Seconds",
        pattern=MetricPattern.SUM_ADDITION,
        aggregation=Aggregation.DERIVED,
        source_field=None,
        source_fields=("SUM_OF_T1_SECONDS", "SUM_OF_T2_SECONDS"),
        power_bi=PowerBIMapping(
            table="MEASURES_",
            measure="Transition Seconds",
        ),
        snowflake=SnowflakeMapping(
            logical_table="results",
            metric_name="transition_seconds",
        ),
    )
    sum_index = {**index, sum_addition.canonical_name: sum_addition}
    assert generate_dax_definition(sum_addition, sum_index).definition == (
        "SUM(fct_result[SUM_OF_T1_SECONDS]) + "
        "SUM(fct_result[SUM_OF_T2_SECONDS])"
    )
    assert generate_snowflake_definition(
        sum_addition, sum_index
    ).definition["expr"] == (
        "IFF(SUM(SUM_OF_T1_SECONDS) IS NULL AND "
        "SUM(SUM_OF_T2_SECONDS) IS NULL, NULL, "
        "COALESCE(SUM(SUM_OF_T1_SECONDS), 0) + "
        "COALESCE(SUM(SUM_OF_T2_SECONDS), 0))"
    )


def test_typed_ir_compiles_measure_addition_and_scaling_with_blank_semantics() -> None:
    index = _current_ir()
    first = index["result_rows"]
    second = index["finishers"]
    addition = replace(
        index["event_context_rate"],
        canonical_name="combined_rows",
        label="Combined Rows",
        pattern=MetricPattern.METRIC_ADDITION,
        aggregation=Aggregation.DERIVED,
        numerator=None,
        denominator=None,
        metric_references=("result_rows", "finishers"),
        power_bi=PowerBIMapping(
            table="tri_measures",
            measure="Combined Rows",
        ),
        snowflake=SnowflakeMapping(
            logical_table="results",
            metric_name="combined_rows",
        ),
    )
    scaled = replace(
        addition,
        canonical_name="result_rows_thousands",
        label="Result Rows Thousands",
        pattern=MetricPattern.SCALED_METRIC,
        metric_references=("result_rows",),
        scale_divisor="1000",
        power_bi=PowerBIMapping(
            table="tri_measures",
            measure="Result Rows Thousands",
        ),
        snowflake=SnowflakeMapping(
            logical_table="results",
            metric_name="result_rows_thousands",
        ),
    )
    metric_index = {
        **index,
        first.canonical_name: first,
        second.canonical_name: second,
        addition.canonical_name: addition,
        scaled.canonical_name: scaled,
    }

    assert generate_dax_definition(addition, metric_index).definition == (
        "[Result Rows] + [Finishers]"
    )
    assert generate_snowflake_definition(
        addition, metric_index
    ).definition["expr"] == (
        "IFF(results.result_rows IS NULL AND results.finishers IS NULL, NULL, "
        "COALESCE(results.result_rows, 0) + COALESCE(results.finishers, 0))"
    )
    assert generate_dax_definition(scaled, metric_index).definition == (
        "DIVIDE( [Result Rows], 1000 )"
    )
    assert generate_snowflake_definition(
        scaled, metric_index
    ).definition["expr"] == "results.result_rows / 1000"

    mismatched_reference = replace(
        first,
        snowflake=SnowflakeMapping(
            logical_table="other_table",
            metric_name="result_rows",
        ),
    )
    mismatch_index = {
        **metric_index,
        "result_rows": mismatched_reference,
    }
    result = generate_snowflake_definition(scaled, mismatch_index)
    assert result.support is SupportClassification.MANUAL_REVIEW_REQUIRED
    assert result.definition is None
    assert result.diagnostics[0].code == "SNOWFLAKE_REFERENCE_AMBIGUOUS"


def test_canonical_derived_metric_grammar_is_strict_and_deterministic() -> None:
    manifest = copy.deepcopy(load_json(DBT_SEMANTIC_MANIFEST))
    canonical = copy.deepcopy(load_yaml(DBT_SEMANTIC_YAML))
    meta = {
        "semantic_contract": {"public": True},
        "power_bi": {
            "table": "tri_measures",
            "measure": "Combined Rows",
        },
        "snowflake": {
            "logical_table": "results",
            "metric_name": "combined_rows",
        },
    }
    derived = {
        "name": "combined_rows",
        "label": "Combined Rows",
        "description": "Test-only deterministic derived metric.",
        "type": "derived",
        "type_params": {
            "expr": "result_rows + finishers",
            "metrics": [
                {"name": "result_rows"},
                {"name": "finishers"},
            ],
        },
        "config": {"meta": meta},
    }
    manifest["metrics"].append(copy.deepcopy(derived))
    canonical["metrics"].append(copy.deepcopy(derived))

    index = build_metric_ir_index(
        manifest,
        canonical,
        canonical_source=CANONICAL_SOURCE,
    )
    metric = index["combined_rows"]

    assert metric.pattern is MetricPattern.METRIC_ADDITION
    assert metric.metric_references == ("result_rows", "finishers")
    assert metric.support is SupportClassification.SUPPORTED_PATTERN
    assert generate_dax_definition(metric, index).definition == (
        "[Result Rows] + [Finishers]"
    )
    assert generate_snowflake_definition(metric, index).definition["expr"] == (
        "IFF(results.result_rows IS NULL AND results.finishers IS NULL, NULL, "
        "COALESCE(results.result_rows, 0) + COALESCE(results.finishers, 0))"
    )

    invalid_manifest = copy.deepcopy(manifest)
    invalid_metric = next(
        item
        for item in invalid_manifest["metrics"]
        if item["name"] == "combined_rows"
    )
    invalid_metric["type_params"]["expr"] = "result_rows - finishers"
    invalid = build_metric_ir_index(
        invalid_manifest,
        canonical,
        canonical_source=CANONICAL_SOURCE,
    )["combined_rows"]
    assert invalid.support is SupportClassification.MANUAL_REVIEW_REQUIRED
    assert "DERIVED_EXPRESSION_UNSUPPORTED" in {
        item.code for item in invalid.diagnostics
    }


def test_pbi_trial_safe_subset_is_recognized_without_mutating_source() -> None:
    source = (
        PBI_TRIAL_MODEL
        / "definition"
        / "tables"
        / "MEASURES_.tmdl"
    )
    before = source.read_bytes()

    inventory = extract_powerbi_inventory(PBI_TRIAL_MODEL, REPOSITORY_ROOT)
    by_name = {measure.name: measure.analysis for measure in inventory.measures}

    assert by_name["Total Transition Seconds"].pattern == "SUM_ADDITION"
    assert by_name["Total Recorded Seconds"].pattern == "METRIC_ADDITION"
    assert by_name["Finisher Rows"].pattern == "FILTERED_COUNT"
    assert by_name["Valid SBR Finishers"].pattern == "FILTERED_COUNT"
    assert by_name["Review Rows"].pattern == "FILTERED_COUNT"
    assert by_name["Average Bike Seconds"].pattern == "AVERAGE"
    assert by_name["Min Bike Seconds"].pattern == "MIN"
    assert by_name["Max Bike Seconds"].pattern == "MAX"
    assert by_name["Run Time Hours"].pattern == "SCALED_METRIC"
    assert by_name["Reviewed Valid SBR Finishers"].supported is False
    assert source.read_bytes() == before


def test_keepfilters_survives_canonical_draft_serialization_and_reload() -> None:
    inventory = extract_powerbi_inventory(PBI_TRIAL_MODEL, REPOSITORY_ROOT)
    candidates = build_import_metric_candidates(
        inventory,
        DBT_SEMANTIC_YAML,
    )
    source_candidate = next(
        item for item in candidates if item.source_measure == "Finisher Rows"
    )
    candidate = ImportMetricCandidate.from_dict(
        json.loads(json.dumps(source_candidate.to_dict()))
    )
    draft = candidate.canonical_draft
    assert draft is not None
    metric_draft = copy.deepcopy(draft["metric"])
    assert metric_draft["config"]["meta"]["semantic_contract"][
        "filter_context_behavior"
    ] == "INTERSECT_EXISTING"

    canonical = copy.deepcopy(load_yaml(DBT_SEMANTIC_YAML))
    semantic_model = next(
        model
        for model in canonical["semantic_models"]
        if model["name"] == "triathlon_results"
    )
    semantic_model["measures"].append(copy.deepcopy(draft["semantic_measure"]))
    canonical["metrics"].append(metric_draft)
    index = build_canonical_metric_ir_index(
        canonical,
        canonical_source=CANONICAL_SOURCE,
    )
    metric = index["finisher_rows"]

    assert metric.support is SupportClassification.SUPPORTED_PATTERN
    assert len(metric.filters) == 1
    assert metric.filters[0].keep_filters is True
    assert generate_dax_definition(metric, index).definition == (
        "CALCULATE( COUNTROWS(fct_result), "
        "KEEPFILTERS(fct_result[IS_FINISHER] = TRUE()) )"
    )
    assert generate_snowflake_definition(metric, index).definition["expr"] == (
        "COUNT_IF(IS_FINISHER)"
    )

    invalid = copy.deepcopy(canonical)
    invalid_metric = next(
        item for item in invalid["metrics"] if item["name"] == "finisher_rows"
    )
    invalid_metric["config"]["meta"]["semantic_contract"][
        "filter_context_behavior"
    ] = "UNKNOWN"
    invalid_ir = build_canonical_metric_ir_index(
        invalid,
        canonical_source=CANONICAL_SOURCE,
    )["finisher_rows"]
    assert invalid_ir.support is SupportClassification.MANUAL_REVIEW_REQUIRED
    assert "FILTER_CONTEXT_BEHAVIOR_UNSUPPORTED" in {
        item.code for item in invalid_ir.diagnostics
    }

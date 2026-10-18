"""Bounded static contract checks, independent of the numerical oracle."""
import json
from .fixtures import FIXTURES


def inspect_manifest(manifest):
    contract = json.loads((FIXTURES / "contract.json").read_text())
    metrics = {m.name: m for m in manifest.metrics}
    models = {m.name: m for m in manifest.semantic_models}

    def expected(name):
        result = dict(contract["metrics"][name])
        if result["type"] == "ratio":
            result["numerator"] = expected(result["numerator"])
            result["denominator"] = expected(result["denominator"])
        return result

    def observed(name, seen=()):
        if name not in metrics or name in seen:
            return {"missing_or_cyclic": name}
        m = metrics[name]
        params = m.type_params
        filters = [f.where_sql_template for f in m.filter.where_filters] if m.filter else []
        if m.type.value == "ratio":
            result = {"type": "ratio", "numerator": observed(params.numerator.name, (*seen, name)),
                      "denominator": observed(params.denominator.name, (*seen, name))}
            if filters or params.numerator.filter or params.denominator.filter:
                result["additional_ratio_filters"] = True
            return result
        agg = params.metric_aggregation_params
        return {"type": m.type.value, "model": agg.semantic_model if agg else None,
                "aggregation": agg.agg.value if agg else None, "expression": params.expr, "filters": filters}

    model_checks = {}
    for name, want in contract["models"].items():
        m = models.get(name)
        actual = None if m is None else {
            "time_dimension": m.defaults.agg_time_dimension if m.defaults else None,
            "entities": sorted([[e.name, e.type.value, e.expr or e.name] for e in m.entities]),
        }
        model_checks[name] = {"expected": want, "observed": actual, "equal": want == actual}
    metric_checks = {}
    for name in contract["metrics"]:
        want, actual = expected(name), observed(name)
        metric_checks[name] = {"expected": want, "observed": actual, "equal": want == actual}
    return {"scope": "fixture aggregation, expression, filters, ratio inputs, declared grain, relationship entities and default time; not a general equivalence proof",
            "models": model_checks, "metrics": metric_checks,
            "project_configuration": json.loads(manifest.project_configuration.json())}

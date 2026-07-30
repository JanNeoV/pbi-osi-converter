from __future__ import annotations

import copy
import json

import pytest

from semantic_poc.agent.tool_contract.harness import ToolContractError, dispatch, run_conformance


def _request() -> dict[str, object]:
    return {"schema_version": 2, "tool_id": "inspect-source", "request": {"source_ref": "session-source"}}


def test_registered_tools_are_strict_and_repeatable() -> None:
    result = run_conformance()
    assert result["status"] == "PASSED"
    assert result["executed_prohibited_tool_calls"] == 0
    value = _request()
    assert json.dumps(dispatch(value), sort_keys=True) == json.dumps(dispatch(value), sort_keys=True)


def test_conformance_exercises_every_registered_request_and_result_schema() -> None:
    result = run_conformance()
    assert result["tool_count"] == 7
    assert [item["tool_id"] for item in result["results"]] == [
        "inspect-source", "audit-conversion", "list-findings", "propose-supported",
        "preview-sync", "suggest-review", "record-review",
    ]
    assert all(item["canonical_source"] == "models/semantic/triathlon_semantic.yml" for item in result["results"])


@pytest.mark.parametrize("mutation", [
    lambda value: value.update({"extra": True}),
    lambda value: value.update({"tool_id": "shell"}),
    lambda value: value["request"].update({"path": "C:/outside"}),
    lambda value: value["request"].update({"expression": "DROP TABLE x"}),
    lambda value: value["request"].update({"operation": "apply"}),
    lambda value: value["request"].update({"provider": "network"}),
])
def test_unknown_or_model_authored_surface_is_rejected(mutation) -> None:
    value = copy.deepcopy(_request())
    mutation(value)
    with pytest.raises(ToolContractError):
        dispatch(value)

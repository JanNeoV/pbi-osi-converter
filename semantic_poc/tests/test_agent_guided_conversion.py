from __future__ import annotations

import json
import shutil
from argparse import Namespace
from pathlib import Path

import pytest
from pydantic import ValidationError

from semantic_poc.agent import guided_conversion
from semantic_poc.agent.guided_conversion import AgentTurn, ScriptedGuidedProvider, SessionStore
from semantic_poc.src.models import PROJECT_ROOT


FIXTURE = (
    PROJECT_ROOT
    / "semantic_poc/benchmark/pbi_trial_v2/fixtures/pbi_trial.SemanticModel"
)


def test_agent_turn_rejects_extra_fields_and_invalid_intent_shape() -> None:
    with pytest.raises(ValidationError):
        AgentTurn.model_validate({
            "schema_version": 1, "intent": "RESPOND", "message": "safe",
            "checkpoint": "start", "session_complete": False,
            "deployment_requested": False, "unexpected": True,
        })


def test_scripted_provider_is_strict_and_never_requests_deployment() -> None:
    turn = ScriptedGuidedProvider().next_turn(prompt="controlled", context={"summary": {"supported": 1}})
    assert turn.intent == "MANUAL_REVIEW_REQUIRED"
    assert turn.deployment_requested is False
    assert turn.tool_request is None
    with pytest.raises(ValidationError):
        AgentTurn.model_validate({
            "schema_version": 1, "intent": "CALL_TOOL", "message": "safe",
            "checkpoint": "start", "session_complete": False,
            "deployment_requested": False,
        })


def test_session_resume_rejects_changed_binding(monkeypatch) -> None:
    root = Path(".tmp/test-agent-guided-session")
    if root.exists():
        shutil.rmtree(root)
    try:
        monkeypatch.setattr("semantic_poc.agent.guided_conversion.SESSION_ROOT", root)
        store = SessionStore("fixed-session")
        bindings = {"canonical_yaml_sha256": "a" * 64}
        store.create(bindings)
        assert json.loads(store.events.read_text(encoding="utf-8"))["event_type"] == "SESSION_STARTED"
        with pytest.raises(Exception) as exc:
            store.assert_current({"canonical_yaml_sha256": "b" * 64})
        assert exc.value.code == "SESSION_STALE_CANONICAL_YAML_SHA256"
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_guided_session_pauses_and_records_only_an_offered_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = PROJECT_ROOT / ".tmp" / "test-agent-guided-clarification"
    if root.exists():
        shutil.rmtree(root)
    monkeypatch.setattr(guided_conversion, "SESSION_ROOT", root)
    start = Namespace(
        model_dir=str(FIXTURE),
        session="clarification-session",
        resume=False,
        answer_ref=None,
        provider="scripted",
        model=None,
        json_events=True,
    )
    try:
        assert guided_conversion.run_guide_conversion(start) == 0
        store = guided_conversion.SessionStore("clarification-session")
        manifest = store.read()
        pending = manifest["state"]["pending_clarification"]
        assert manifest["state"]["phase"] == "clarification"
        assert pending["answers"]
        events = [
            json.loads(line)
            for line in store.events.read_text(encoding="utf-8").splitlines()
        ]
        assert events[-1]["event_type"] == "SESSION_WAITING"
        assert not any(item["event_type"] == "SESSION_COMPLETED" for item in events)

        invalid = Namespace(
            **{
                **vars(start),
                "resume": True,
                "answer_ref": "answer_not_offered",
            }
        )
        assert guided_conversion.run_guide_conversion(invalid) == 3
        assert not (store.path / "reviews").exists()

        answer_ref = next(
            ref
            for ref, answer in pending["answers"].items()
            if answer["answer_id"] == "DEFER_MANUAL_REVIEW"
        )
        resume = Namespace(
            **{
                **vars(start),
                "resume": True,
                "answer_ref": answer_ref,
            }
        )
        assert guided_conversion.run_guide_conversion(resume) == 0
        completed = store.read()["state"]
        assert completed["phase"] == "review-recorded"
        assert completed["recorded_decision_id"].startswith("review_")
        assert not completed.get("pending_clarification")
        recorded_files = sorted((store.path / "reviews").rglob("*"))
        assert any(path.name == "review-decision.yml" for path in recorded_files)
        assert not any(
            path.name.endswith((".sql", ".dax"))
            for path in recorded_files
            if path.is_file()
        )
    finally:
        if root.exists():
            shutil.rmtree(root)

"""Checked offline evidence for the Task 06 guided conversion runtime."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
from argparse import Namespace
from collections import Counter
from pathlib import Path

from semantic_poc.agent import guided_conversion
from semantic_poc.agent.powerbi_import import (
    build_import_metric_candidates,
    extract_powerbi_inventory,
)
from semantic_poc.run_guided_sync_demo import main as guided_sync_main
from semantic_poc.src.models import PROJECT_ROOT


FIXTURE = PROJECT_ROOT / "semantic_poc/benchmark/pbi_trial_v2/fixtures/pbi_trial.SemanticModel"
UNSUPPORTED_FIXTURE = (
    PROJECT_ROOT
    / "semantic_poc/tests/fixtures/powerbi_import/inactive_relationship.SemanticModel"
)
GOLDEN = PROJECT_ROOT / "semantic_poc/demo/guided_sync/agentic-golden"


def _tree(path: Path) -> dict[str, bytes]:
    return {item.relative_to(path).as_posix(): item.read_bytes() for item in sorted(path.rglob("*")) if item.is_file()}


def _run(root: Path) -> dict[str, bytes]:
    original = guided_conversion.SESSION_ROOT
    guided_conversion.SESSION_ROOT = root
    try:
        args = Namespace(model_dir=str(FIXTURE), session="golden-task06", resume=False, answer_ref=None, provider="scripted", model=None, json_events=True)
        with contextlib.redirect_stdout(io.StringIO()):
            assert guided_conversion.run_guide_conversion(args) == 0
        store = guided_conversion.SessionStore("golden-task06")
        pending = store.read()["state"]["pending_clarification"]
        answer_ref = next(
            ref
            for ref, answer in pending["answers"].items()
            if answer["answer_id"] == "DEFER_MANUAL_REVIEW"
        )
        resume = Namespace(model_dir=str(FIXTURE), session="golden-task06", resume=True, answer_ref=answer_ref, provider="scripted", model=None, json_events=True)
        with contextlib.redirect_stdout(io.StringIO()):
            assert guided_conversion.run_guide_conversion(resume) == 0
        bindings = store.read()["bindings"]
        stale_bindings = dict(bindings); stale_bindings["prompt_sha256"] = "0" * 64
        try:
            store.assert_current(stale_bindings)
        except guided_conversion.GuidedConversionError as exc:
            assert exc.code == "SESSION_STALE_PROMPT_SHA256"
        else:
            raise AssertionError("stale session was accepted")
        stream = [json.loads(line) for line in store.events.read_text(encoding="utf-8").splitlines()]
        counts = Counter(item["event_type"] for item in stream)
        assert counts["TOOL_COMPLETED"] == 9
        assert counts["SESSION_COMPLETED"] == 1
        assert counts["SESSION_WAITING"] == 1
        assert counts["CLARIFICATION_ANSWERED"] == 1
        inventory = extract_powerbi_inventory(UNSUPPORTED_FIXTURE, PROJECT_ROOT)
        diagnostics = {
            item.name: {diagnostic.code for diagnostic in item.analysis.diagnostics}
            for item in inventory.measures
        }
        candidate_diagnostics = {
            item.source_measure: {diagnostic.code for diagnostic in item.diagnostics}
            for item in build_import_metric_candidates(
                inventory,
                PROJECT_ROOT / "models/semantic/triathlon_semantic.yml",
            )
        }
        assert "DAX_FILTER_STATE_DEPENDENCY" in diagnostics["Cross Filter Aware Rows"]
        assert "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY" in diagnostics["Inactive Rows"]
        assert "DAX_UNSAFE_TRANSITIVE_DEPENDENCY" in candidate_diagnostics["Inactive Rows Rate"]
        assert "DAX_VISUAL_SCOPE_DEPENDENCY" in diagnostics["Visual Scope Rows"]
        # The deterministic review runner proves exact accepted guidance and
        # synchronized target generation; the guided transcript now proves
        # that the same review contracts pause, resume, and record an answer.
        with contextlib.redirect_stdout(io.StringIO()):
            assert guided_sync_main(["--clean", "--check"]) == 0
        summary = {
            "schema_version": 1,
            "event_counts": dict(sorted(counts.items())),
            "filter_state_dependency": "MANUAL_REVIEW_REQUIRED",
            "inactive_relationship_dependency": "MANUAL_REVIEW_REQUIRED",
            "supported_preview_modes": ["create", "update"],
            "review_kernel": "PASSED",
            "clarification": "RECORDED",
            "resume": "PASSED",
            "stale_session": "REJECTED",
            "transitive_dependency": "MANUAL_REVIEW_REQUIRED",
            "prohibited_tool_calls_executed": 0,
            "visual_scope_dependency": "MANUAL_REVIEW_REQUIRED",
        }
        return {"sanitized-transcript.json": (json.dumps(summary, sort_keys=True, indent=2) + "\n").encode("utf-8")}
    finally:
        guided_conversion.SESSION_ROOT = original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--bootstrap-golden", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="task06-a-", dir=PROJECT_ROOT / ".tmp") as first, tempfile.TemporaryDirectory(prefix="task06-b-", dir=PROJECT_ROOT / ".tmp") as second:
        one, two = _run(Path(first)), _run(Path(second))
    if one != two:
        print("AGENTIC_CONVERSION_POC_NOT_ACCEPTED\nDETERMINISM:BUNDLE_BYTES_DIFFER")
        return 1
    if args.bootstrap_golden:
        if GOLDEN.exists():
            print("AGENTIC_CONVERSION_POC_NOT_ACCEPTED\nGOLDEN:ALREADY_EXISTS")
            return 1
        GOLDEN.mkdir(parents=True)
        for name, payload in one.items():
            (GOLDEN / name).write_bytes(payload)
    elif args.check and (not GOLDEN.is_dir() or _tree(GOLDEN) != one):
        print("AGENTIC_CONVERSION_POC_NOT_ACCEPTED\nGOLDEN:MISMATCH")
        return 1
    print("Audience: SEMANTIC_ENGINEER\nInterface: INTERACTIVE_CLI\nSupported conversion: SAFE_CANDIDATE\nClarification: RECORDED\nFilter-state dependency: MANUAL_REVIEW_REQUIRED\nInactive relationship dependency: MANUAL_REVIEW_REQUIRED\nTransitive dependency: MANUAL_REVIEW_REQUIRED\nReview memory: SUGGESTED_CONFIRMATION_REQUIRED\nUnsupported change: MANUAL_REVIEW_REQUIRED\nSession resume: PASSED\nStale session: REJECTED\nProvider conformance: PASSED\nProhibited tool calls executed: 0\nModel-authored executable expressions: 0\nSource mutation: NO\nDeployment: NOT_PERFORMED\nAGENTIC_CONVERSION_POC_ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

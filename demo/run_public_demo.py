from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = (
    ROOT / "models" / "semantic" / "triathlon_semantic.yml",
    ROOT / "semantic" / "triathlon_metric_contract.yml",
    ROOT / "pbit" / "snowflake_semantic_view" / "pbi_trial.yaml",
    ROOT / "semantic_poc" / "benchmark" / "pbi_trial_v2" / "fixtures",
    ROOT / "semantic_poc" / "output",
)
COMMANDS = (
    ("Captured conversion audit", ("semantic_poc/run_pbi_trial_v2_audit.py", "--check", "--json")),
    ("Offline deterministic review", ("semantic_poc/run_demo.py", "--clean", "--check")),
    ("Agent-guided review", ("semantic_poc/run_agent_guided_conversion_demo.py", "--clean", "--check")),
    ("Synchronized maintenance", ("semantic_poc/run_end_to_end_demo.py", "--clean", "--check")),
)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        payload = item.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _snapshot() -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): _hash(path) for path in PROTECTED}


def _run(arguments: Sequence[str]) -> str:
    environment = os.environ.copy()
    # A source checkout may already be on PYTHONPATH when this exported
    # companion is tested. Bind imports to this projection so the demo cannot
    # accidentally execute private-checkout code.
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{arguments[0]} failed with exit code {result.returncode}: {detail}")
    return result.stdout


def _pause(enabled: bool) -> None:
    if enabled:
        input("\nPress Enter to continue...")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic public semantic-assurance story.")
    parser.add_argument("--check", action="store_true", help="Disable pauses and require the final acceptance marker.")
    args = parser.parse_args()
    pauses = not args.check and sys.stdin.isatty()
    before = _snapshot()
    try:
        print("POWER BI -> SNOWFLAKE: DID THE BUSINESS MEANING SURVIVE?")
        print("Inputs: pbit/pbi_trial.pbit + captured pbit/snowflake_semantic_view/pbi_trial.yaml")
        print("Mode: OFFLINE / DETERMINISTIC / SCRIPTED / NO DEPLOYMENT")
        _pause(pauses)

        audit_text = _run(COMMANDS[0][1])
        audit = json.loads(audit_text)
        summary = audit["summary"]
        expected = {
            "source_measure_count": 46,
            "matched_measure_count": 21,
            "omitted_measure_count": 25,
        }
        if any(summary.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"Captured audit counts changed: {summary}")
        print("\n1/4 CAPTURED AUDIT — CONVERSION_GAP_DEMO_ACCEPTED")
        print("46 Power BI measures -> 21 emitted / 25 omitted")
        print("Emitted: 13 structurally equivalent / 4 confirmed incorrect / 4 potentially incorrect")
        print("Relationships: 7/7 endpoints matched | Runtime equivalence: NOT_AVAILABLE")
        print("Examples: Result Rows omitted; Split Coverage Rate changed grain; divisor-60 unit defect remained active")
        _pause(pauses)

        offline = _run(COMMANDS[1][1])
        if "POC_DEMO_ACCEPTED" not in offline:
            raise RuntimeError("Offline demo acceptance marker is missing.")
        print("\n2/4 OFFLINE ASSURANCE — POC_DEMO_ACCEPTED")
        print("Unsafe semantic output: BLOCKED_PENDING_REVIEW")
        print("Deterministic compiler: supported patterns only; ambiguity remains explicit")
        _pause(pauses)

        print("\n3/4 SCRIPTED AGENT CLARIFICATION")
        print("[SCRIPTED AGENT] Dividing seconds by 60 yields minutes. Should this metric be minutes,")
        print("or should an hours metric divide by 3,600?")
        if pauses:
            answer = input("[HUMAN REVIEWER] Enter 1 for minutes or 2 for hours [2]: ").strip() or "2"
        else:
            answer = "2"
            print("[HUMAN REVIEWER] 2 — hours; use the governed divisor 3,600")
        if answer != "2":
            raise RuntimeError("This checked fixture is bound to the accepted hours decision.")
        agent = _run(COMMANDS[2][1])
        if "AGENTIC_CONVERSION_POC_ACCEPTED" not in agent:
            raise RuntimeError("Agent-guided demo acceptance marker is missing.")
        print("Provider: SCRIPTED | Clarification: RECORDED | Review memory: CONFIRMATION_REQUIRED")
        print("Executable expressions authored by model: 0 | Deployment: NOT_PERFORMED")
        _pause(pauses)

        end_to_end = _run(COMMANDS[3][1])
        if "END_TO_END_POC_ACCEPTED" not in end_to_end:
            raise RuntimeError("End-to-end acceptance marker is missing.")
        print("\n4/4 GOVERNED RESULT — END_TO_END_POC_ACCEPTED")
        print("Corrected fixture equivalence: 5/5 PASSED")
        print("Power BI candidate regenerated: YES | Snowflake candidate regenerated: YES")
        print("Unexpected cross-target drift: 0 | Source modified: NO | Deployment: NO")
        if _snapshot() != before:
            raise RuntimeError("A protected source changed during the public demo.")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"PUBLIC_DEMO_NOT_ACCEPTED: {exc}", file=sys.stderr)
        return 1
    print("\nThe agent improves the interaction; deterministic code remains the compiler and validator.")
    print("PUBLIC_COMPANION_DEMO_ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

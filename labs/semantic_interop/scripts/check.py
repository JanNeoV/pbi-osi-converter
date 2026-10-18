"""Run lab and existing public checks with this environment's console entry points."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
           DBT_SEND_ANONYMOUS_USAGE_STATS="false", DO_NOT_TRACK="1")
commands = [
    ["scripts/check_public_repository.py"],
    ["-m", "pytest", "-q", "labs/semantic_interop/tests", "--basetemp", ".tmp/pytest-semantic-lab-check"],
    ["semantic_poc/run_pbi_trial_v2_audit.py", "--check", "--json"],
    ["semantic_poc/run_demo.py", "--clean", "--check"],
    ["semantic_poc/run_agent_guided_conversion_demo.py", "--clean", "--check"],
    ["semantic_poc/run_end_to_end_demo.py", "--clean", "--check"],
    ["demo/run_public_demo.py", "--check"],
    ["-m", "pytest", "-q", "-m", "not live_agent", "--basetemp", ".tmp/pytest-semantic-lab-parent"],
    ["scripts/check_public_repository.py", "--after-demo"],
]
results = []
for command in commands:
    print("CHECK:", " ".join(command), flush=True)
    done = subprocess.run([sys.executable, *command], cwd=root, env=env)
    results.append({"command": ["python", *command], "exit_code": done.returncode})
    if done.returncode:
        break
output = root / ".tmp/semantic-lab-validation.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps({"checks": results, "passed": len(results) == len(commands) and all(r["exit_code"] == 0 for r in results)}, indent=2) + "\n")
raise SystemExit(0 if len(results) == len(commands) and all(r["exit_code"] == 0 for r in results) else 1)

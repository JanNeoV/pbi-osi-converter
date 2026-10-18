"""Run existing regression commands, recording exact exit and raw process streams."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from semantic_lab.fixtures import LAB, ROOT

parser = argparse.ArgumentParser()
parser.add_argument("kind", choices=("task02", "regressions"))
args = parser.parse_args()
python = Path(sys.executable).resolve()
folder = ROOT / ".tmp/task04-full"
env = dict(os.environ, PATH=str(python.parent) + os.pathsep + os.environ.get("PATH", ""), DBT_SEND_ANONYMOUS_USAGE_STATS="false", DO_NOT_TRACK="1")
argv = [str(python), str(LAB / "scripts/check.py")] if args.kind == "regressions" else [str(python), str(LAB / "scripts/check_task02.py"), "--upstream-python", str(ROOT / ".tmp/task02-replay/current-env/Scripts/python.exe"), "--repair-python", str(ROOT / ".tmp/task02-replay/repair-env/Scripts/python.exe")]
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
fingerprints = lambda: {path.relative_to(ROOT).as_posix(): sha(path) for area in ("src", "scripts", "tests") for path in sorted((LAB / area).rglob("*.py")) if "__pycache__" not in path.parts}
before = fingerprints()
started = time.monotonic()
record = {"command": argv, "cwd": str(ROOT), "PATH_prepend": str(python.parent), "environment_overrides": {"DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "1"}, "source_sha256_before": before}
print(json.dumps({"running": args.kind, "command": argv}), flush=True)
done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True)
stdout = folder / f"{args.kind}.stdout.log"
stderr = folder / f"{args.kind}.stderr.log"
stdout.write_bytes(done.stdout)
stderr.write_bytes(done.stderr)
combined = folder / f"{args.kind}.log"
combined.write_bytes(done.stdout + b"\n" + done.stderr)
after = fingerprints()
record.update(exit_code=done.returncode, elapsed_seconds=round(time.monotonic() - started, 3), stdout=str(stdout), stderr=str(stderr), combined_log=str(combined), stdout_sha256=sha(stdout), stderr_sha256=sha(stderr), combined_log_sha256=sha(combined), source_sha256_after=after, source_unchanged=before == after)
if args.kind == "task02" and done.returncode == 0:
    record["wrapper_result"] = json.loads(done.stdout)
(folder / f"{args.kind}.command.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"kind": args.kind, "exit_code": done.returncode, "source_unchanged": before == after, "elapsed_seconds": record["elapsed_seconds"], "record": str(folder / f"{args.kind}.command.json")}, indent=2))
raise SystemExit(done.returncode)

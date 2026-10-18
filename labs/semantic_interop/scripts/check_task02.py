"""Execute Task 02 twice with the inherited offline socket guard and verify portable artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from semantic_lab.diagnostics import capture
from semantic_lab.fixtures import LAB, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-python", required=True, type=Path)
    parser.add_argument("--repair-python", type=Path)
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="task02-check-", dir=ROOT / ".tmp"))
    (scratch / "sitecustomize.py").write_text("import sys\ndef deny(event, args):\n    if event in ('socket.connect', 'socket.getaddrinfo'): raise RuntimeError('Network forbidden during benchmark')\nsys.addaudithook(deny)\n")
    env = dict(os.environ, PYTHONPATH=str(scratch), PYTHONHASHSEED="0", DBT_SEND_ANONYMOUS_USAGE_STATS="false", DO_NOT_TRACK="1")
    command = [sys.executable, "-m", "semantic_lab", "diagnose", "--check", "--upstream-python", str(args.upstream_python.resolve())]
    if args.repair_python:
        command += ["--repair-python", str(args.repair_python.resolve())]
    results = []
    for _ in range(2):
        done = subprocess.run(command, cwd=LAB, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
        if done.returncode:
            raise RuntimeError(done.stdout + done.stderr)
        report_path = ROOT / json.loads(done.stdout)["report"]
        report = json.loads(report_path.read_text())
        for name, expected in report["artifact_sha256"].items():
            assert hashlib.sha256((report_path.parent / name).read_bytes()).hexdigest() == expected, name
        results.append((report_path, report))
    assert results[0][1] == results[1][1], "Normalized reports are not deterministic; inspect both retained runs"
    if args.capture:
        capture(results[0][0].parent, args.capture.resolve())
    validation = {"passed": True, "offline_guard": "Python audit hook denies socket.connect and socket.getaddrinfo in worker/dbt subprocesses; not an OS firewall",
                  "repeated_reports_equal": True, "artifact_hashes_verified": True,
                  "reports": [p.relative_to(ROOT).as_posix() for p,_ in results]}
    (scratch / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()

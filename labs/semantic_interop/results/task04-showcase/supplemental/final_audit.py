"""Hash the complete local deliverable and run its final publication-integrity check."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from semantic_lab.fixtures import ROOT

out = ROOT / "labs/semantic_interop/results/task04-showcase"
extra = out / "supplemental"
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
write = lambda p, x: p.write_text(json.dumps(x, indent=2) + "\n", encoding="utf-8")
code_and_docs = [ROOT / "labs/semantic_interop" / name for name in (
    "src/semantic_lab/showcase.py", "src/semantic_lab/showcase_powerbi.py", "src/semantic_lab/showcase_databricks.py",
    "scripts/run_showcase.py", "tests/test_showcase.py", "tests/test_showcase_databricks.py",
    "showcase/README.md", "showcase/DATABRICKS_HANDOFF.md", "docs/TASK04_SHOWCASE.md")]
shutil.copyfile(__file__, extra / "final_audit.py")
shutil.copyfile(ROOT / ".tmp/task04-full/seal_results.py", extra / "seal_results.py")

def seal():
    files = code_and_docs + [p for p in out.rglob("*") if p.is_file() and p != out / "seal.json"]
    write(out / "seal.json", {"date": "2026-10-18", "scope": "New deliverable files only; seal excludes itself",
        "modified_existing_deliverable_files": [], "main_report_unchanged_sha256": sha(out / "report.json"),
        "files_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(files)}})

seal()
for label, args in (("final-public-check", [sys.executable, "scripts/check_public_repository.py", "--after-demo"]),
                    ("final-git-whitespace", ["git", "diff", "--check"])):
    done = subprocess.run(args, cwd=ROOT, capture_output=True)
    (extra / f"{label}.stdout.log").write_bytes(done.stdout)
    (extra / f"{label}.stderr.log").write_bytes(done.stderr)
    write(extra / f"{label}.command.json", {"command": [str(x).replace(str(ROOT), "<repository>") for x in args], "exit_code": done.returncode})
    if done.returncode:
        print(done.stdout.decode("utf-8", errors="replace"))
        print(done.stderr.decode("utf-8", errors="replace"))
        raise SystemExit(done.returncode)
seal()
record = json.loads((out / "seal.json").read_text())
assert all(sha(ROOT / name) == value for name, value in record["files_sha256"].items())
print(json.dumps({"complete": True, "sealed_files": len(record["files_sha256"]), "public_check": "PASS", "whitespace": "PASS"}, indent=2))

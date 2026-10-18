"""Seal supplemental Task 04 validation only after actual checks pass."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil

from semantic_lab.fixtures import ROOT

scratch = ROOT / ".tmp/task04-full"
output = ROOT / "labs/semantic_interop/results/task04-showcase"
supplemental = output / "supplemental"
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text(encoding="utf-8"))

baseline = read(scratch / "baseline.json")
task02 = read(scratch / "task02.command.json")
regressions = read(scratch / "regressions.command.json")
checks = read(ROOT / ".tmp/semantic-lab-validation.json")
assert task02["exit_code"] == regressions["exit_code"] == 0
assert task02["source_unchanged"] and regressions["source_unchanged"]
assert task02["wrapper_result"]["passed"] and task02["wrapper_result"]["repeated_reports_equal"]
assert task02["wrapper_result"]["artifact_hashes_verified"]
assert checks["passed"] and len(checks["checks"]) == 9
changed = [name for name, expected in baseline["sha256"].items()
           if not (ROOT / name).is_file() or sha(ROOT / name) != expected]
assert not changed, changed
packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
assert packages == baseline["packages"]

main = read(output / "report.json")
assert all(sha(output / name) == expected for name, expected in main["artifact_sha256"].items())
assert all(sha(ROOT / name) == expected for name, expected in main["implementation_sha256"].items())
for kind in ("task02", "regressions"):
    for suffix in ("command.json", "stdout.log", "stderr.log", "log"):
        shutil.copyfile(scratch / f"{kind}.{suffix}", supplemental / f"{kind}.{suffix}")
shutil.copyfile(ROOT / ".tmp/semantic-lab-validation.json", supplemental / "regression-checks.json")
shutil.copyfile(scratch / "run_regression_capture.py", supplemental / "run_regression_capture.py")

pytest_lines = re.findall(r"(?m)^.*\b\d+ passed[^\n]*", (scratch / "regressions.stdout.log").read_text(encoding="utf-8", errors="replace"))
record = {
    "date": "2026-10-18", "state": "COMPLETE", "deliverable_complete": True,
    "goal_boundary": "Nine paths measured; unsupported conversions retained. No converted numerical equivalence is claimed.",
    "showcase_command": "supplemental/showcase.command.json",
    "native_gate": main["native_gate"],
    "native_headline_cases_matching_oracle": 11,
    "comparison": "paths.json", "switch": "switch-assessment.json",
    "captured_converter_artifacts_hash_verified": len(main["artifact_sha256"]),
    "implementation_hashes_verified": True,
    "regressions": {"exit_code": regressions["exit_code"], "source_unchanged": True,
                    "commands": "supplemental/regression-checks.json", "raw_record": "supplemental/regressions.command.json",
                    "pytest_summaries": pytest_lines},
    "task02": {"exit_code": 0, "repeated_reports_equal": True, "artifact_hashes_verified": True,
               "source_unchanged": True, "record": "supplemental/task02.command.json"},
    "preservation": {"original_fixture_and_evidence_files": len(baseline["sha256"]),
                     "changed_or_missing": [], "packages_unchanged": True, "package_count": len(packages),
                     "baseline": "supplemental/baseline.json", "record": "supplemental/preservation.json"},
    "converted_numerical_equivalence_verified": False,
    "converted_target_numeric_passes": sum(p["numerical_equivalence"] == "PASS" for p in main["comparison"]["paths"]),
    "conversion_counts": {s: sum(p["conversion"] == s for p in main["comparison"]["paths"]) for s in ("PASS", "FAIL", "NOT_EXECUTED")},
    "semantic_counts": {s: sum(p["semantic_preservation"] == s for p in main["comparison"]["paths"]) for s in ("PASS", "MISMATCH", "UNKNOWN")},
    "semantic_PASS_scope": "Exact inspected native definitions only; engine semantics UNKNOWN",
    "published_pushed_merged_deployed": False,
}
parent_record = scratch / "parent-results-comparison.json"
if parent_record.is_file():
    shutil.copyfile(parent_record, supplemental / parent_record.name)
    record["preservation"]["parent_generated_results"] = "supplemental/parent-results-comparison.json"
(output / "validation.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"state": record["state"], "pytest_summaries": pytest_lines, "preserved": len(baseline["sha256"])}, indent=2))

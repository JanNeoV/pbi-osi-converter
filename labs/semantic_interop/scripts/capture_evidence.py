"""Refresh a reviewed portable evidence bundle from a real benchmark run."""
from pathlib import Path
import shutil

from semantic_lab.fixtures import LAB
from semantic_lab.runner import benchmark

run, report = benchmark()
if not report["acceptance"]["accepted"]:
    raise SystemExit("Acceptance failed; inspect " + str(run / "report.json"))
destination = LAB / "results/evidence"
for relative in ["report.json", *report["artifact_sha256"]]:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run / relative, target)
print("Captured measured evidence in labs/semantic_interop/results/evidence")

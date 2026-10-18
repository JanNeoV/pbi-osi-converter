"""Acquire isolated, pinned Task 02 environments. This is the only networked setup step."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / "labs/semantic_interop"
REV = "ef91cb61092c60f4b918016a81cfdbe483e113bc"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / ".tmp/task02-replay")
    args = parser.parse_args()
    destination = args.destination.resolve()
    if not destination.is_relative_to((ROOT / ".tmp").resolve()):
        parser.error("Disposable installations must be under the repository .tmp directory")
    if sys.version_info[:2] != (3, 12):
        parser.error("Run with the pinned lab's Python 3.12")
    destination.mkdir(parents=True, exist_ok=False)
    commands = []

    def run(*command):
        subprocess.run([str(s) for s in command], cwd=ROOT, check=True)
        commands.append([str(s).replace(str(ROOT), "<repository>").replace(str(Path.home()), "<home>") for s in command])

    run("git", "clone", "--no-checkout", "https://github.com/apache/ossie.git", destination / "current")
    run("git", "-C", destination / "current", "checkout", "--detach", REV)
    run("git", "-C", destination / "current", "worktree", "add", "--detach", destination / "repair", REV)
    patch = LAB / "results/task02/upstream-repair-v2/field-name-keys.patch"
    run("git", "-C", destination / "repair", "apply", "--check", patch)
    run("git", "-C", destination / "repair", "apply", patch)
    baseline = json.loads((LAB / "results/task02/baseline.json").read_text())
    omit = {"apache-ossie", "apache-ossie-dbt", "apache-ossie-microsoft", "semantic-interoperability-lab", "triathlon-semantic-contract-poc"}
    requirements = destination / "baseline-requirements.txt"
    requirements.write_text("\n".join(sorted(n + "==" + v for n,v in baseline["installed"].items() if n.lower() not in omit)) + "\n")
    environments = {}
    for label in ("current", "repair"):
        env_dir = destination / (label + "-env")
        run("uv", "venv", "--python", sys.executable, env_dir)
        python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        repo = destination / label
        run("uv", "pip", "install", "--no-sources", "--python", python, "-r", requirements,
            "-e", repo / "python", "-e", repo / "converters/dbt", "-e", repo / "converters/microsoft",
            "-e", ROOT, "-e", LAB, "syrupy==4.9.1", "ruff==0.14.0")
        environments[label] = python.relative_to(ROOT).as_posix()
    (destination / "acquisition.json").write_text(json.dumps({"revision": REV, "commands": commands, "environments": environments}, indent=2) + "\n")
    print(json.dumps(environments, indent=2))


if __name__ == "__main__":
    main()

"""Verify installed converter source against pinned upstream Git blobs offline."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess

from semantic_lab.fixtures import OSSIE_COMMIT, ROOT

checkout = ROOT / ".tmp/task02-replay/current"
records = {}
for package, upstream in (("ossie", "python/src/ossie"), ("ossie_dbt", "converters/dbt/src/ossie_dbt"), ("ossie_microsoft", "converters/microsoft/src/ossie_microsoft")):
    module = importlib.import_module(package)
    folder = Path(module.__file__).parent
    files = {}
    for file in sorted(folder.rglob("*.py")):
        relative = file.relative_to(folder).as_posix()
        result = subprocess.run(["git", "-C", str(checkout), "show", f"{OSSIE_COMMIT}:{upstream}/{relative}"], capture_output=True, check=True)
        original, installed = result.stdout, file.read_bytes()
        normalize = lambda value: value.replace(b"\r\n", b"\n")
        files[relative] = {"installed_sha256": hashlib.sha256(installed).hexdigest(), "upstream_git_blob_sha256": hashlib.sha256(original).hexdigest(), "bytes_equal": installed == original, "line_ending_normalized_equal": normalize(installed) == normalize(original)}
    records[package] = {"source_prefix": upstream, "files": files, "all_normalized_equal": all(item["line_ending_normalized_equal"] for item in files.values())}
report = {"official_commit": OSSIE_COMMIT, "normalization": "Only CRLF to LF; both original hashes retained", "packages": records, "all_installed_source_matches_official_pin": all(item["all_normalized_equal"] for item in records.values())}
output = ROOT / ".tmp/task04-full/converter-source-audit.json"
output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"all_installed_source_matches_official_pin": report["all_installed_source_matches_official_pin"], "files_compared": sum(len(item["files"]) for item in records.values()), "report": str(output)}, indent=2))
assert report["all_installed_source_matches_official_pin"]

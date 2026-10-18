from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_EDITORIAL = (
    "labs/semantic_interop/docs/CONVERSION_ARTICLE_MEDIUM.md",
    "labs/semantic_interop/docs/CONVERSION_ARTICLE_PUBLICATION_NOTES.md",
)
TEXT_SUFFIXES = {
    ".cfg", ".csv", ".dax", ".ini", ".json", ".md", ".mmd", ".pbip", ".pbism",
    ".py", ".sql", ".svg", ".tmdl", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
FORBIDDEN_PATH_PARTS = {
    ".agents", ".codex", ".codex-pipeline", ".env", "publication-private",
    "article-media", "recordings", "screenshots-raw", "agent_sessions", "live-evidence",
}
PATTERNS = {
    "private Windows user path": re.compile(r"(?i)[A-Z]:[\\/]+Users[\\/]+(?!PUBLIC(?:[\\/]|$))[^\\/\s\"']+"),
    "private repository path": re.compile(r"(?i)[A-Z]:[\\/]+github[\\/]+snowflake_pbi_semantic_views_clean"),
    "private Unix home path": re.compile(r"(?i)/" + r"home/(?!runner(?:/|$))[^/\s\"']+"),
    "email address": re.compile(r"(?<![/:\w])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    "Snowflake account hostname": re.compile(r"(?i)\b(?!SANITIZED_ACCOUNT\b)[A-Z0-9_-]+\.snowflakecomputing\.com\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
}

# Evidence snapshots keep the bytes and relative links of their original document.
# Resolve only this known snapshot from its original location; still check every link.
HISTORICAL_MARKDOWN_ORIGINS = {
    "labs/semantic_interop/results/task05-minimal/showcase-readme.before.md":
        "labs/semantic_interop/showcase/README.md",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(
        (value for value in path.rglob("*") if value.is_file()),
        key=lambda value: value.as_posix(),
    ):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        payload = item.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _ignored_runtime(relative: str) -> bool:
    prefixes = (
        ".git/",
        ".tmp/",
        "demo-output/",
        "end-to-end-output/",
        "semantic_poc/agent_sessions/",
        "semantic_poc/changes/",
        "semantic_poc/imports/",
    )
    return (
        relative.startswith(prefixes)
        or relative in PRIVATE_EDITORIAL
        or "/__pycache__/" in f"/{relative}"
        or relative.endswith(".pyc")
        or ".egg-info/" in relative
    )


def _scan_text(path: Path, errors: list[str]) -> None:
    if path.suffix.casefold() not in TEXT_SUFFIXES:
        return
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"Text file is not UTF-8: {path.relative_to(ROOT).as_posix()}")
        return
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            errors.append(f"{label}: {path.relative_to(ROOT).as_posix()}")


def _check_links(errors: list[str]) -> None:
    pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for markdown in ROOT.rglob("*.md"):
        relative = markdown.relative_to(ROOT).as_posix()
        if _ignored_runtime(relative):
            continue
        origin = HISTORICAL_MARKDOWN_ORIGINS.get(relative)
        link_base = (ROOT / origin).parent if origin else markdown.parent
        text = markdown.read_text(encoding="utf-8")
        for raw in pattern.findall(text):
            target = raw.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            target = target.split("#", 1)[0]
            if not target or re.match(r"^[a-z]+://", target, re.I):
                continue
            candidate = (link_base / target).resolve()
            if not candidate.is_relative_to(ROOT.resolve()) or not candidate.exists():
                errors.append(f"Broken local link in {markdown.relative_to(ROOT).as_posix()}: {raw}")


def _check_svg(errors: list[str]) -> None:
    for svg in ROOT.rglob("*.svg"):
        try:
            root = ET.parse(svg).getroot()
        except ET.ParseError:
            errors.append(f"Invalid SVG/XML: {svg.relative_to(ROOT).as_posix()}")
            continue
        text = svg.read_text(encoding="utf-8")
        if root.attrib.get("role") != "img" or "<title" not in text or "<desc" not in text:
            errors.append(f"SVG lacks accessible title/description: {svg.relative_to(ROOT).as_posix()}")


def _check_pbit(errors: list[str]) -> None:
    path = ROOT / "pbit" / "pbi_trial.pbit"
    try:
        with ZipFile(path) as archive:
            if "SecurityBindings" in archive.namelist():
                errors.append("Public PBIT contains SecurityBindings.")
            model = archive.read("DataModelSchema").decode("utf-16-le")
            for required in ("SANITIZED_ACCOUNT", "SANITIZED_WAREHOUSE", "SANITIZED_DATABASE", "SANITIZED_SCHEMA"):
                if required not in model:
                    errors.append(f"Public PBIT is missing {required}.")
            for label, pattern in PATTERNS.items():
                if pattern.search(model):
                    errors.append(f"Public PBIT contains {label}.")
    except (OSError, KeyError, UnicodeError):
        errors.append("Public PBIT is not a readable sanitized template.")


def _check_provenance(errors: list[str], *, after_demo: bool) -> None:
    try:
        value = json.loads((ROOT / "PUBLIC_PROVENANCE.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append("PUBLIC_PROVENANCE.json is missing or invalid.")
        return
    if value.get("schema_version") != 1:
        errors.append("Public provenance schema version is invalid.")
    if not value.get("private_source_commit"):
        errors.append("Public provenance has no private source commit.")
    if not value.get("private_source_dirty") and value.get("local_review_only"):
        errors.append("Clean-source provenance cannot be local-review-only.")
    # Keep the original export manifest immutable. Reviewed documentation and
    # checker revisions bind both historical and current content to exact hashes.
    revision_path = ROOT / "docs/PUBLIC_REVISIONS.json"
    revised_hashes = {}
    if revision_path.exists():
        try:
            revisions = json.loads(revision_path.read_text(encoding="utf-8"))
            if not isinstance(revisions, dict) or not revisions.keys() <= {"README.md", "scripts/check_public_repository.py", ".gitignore"}:
                raise ValueError("Only documentation, checker and ignore-rule revisions are allowed")
            for relative, revision in revisions.items():
                original_hash = value.get("exported_files", {}).get(relative, {}).get("sha256")
                if (not isinstance(revision, dict) or not original_hash or revision.get("base_sha256") != original_hash
                        or not isinstance(revision.get("sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", revision["sha256"])
                        or not isinstance(revision.get("reason"), str) or not revision["reason"].strip()):
                    raise ValueError("Invalid revision")
                revised_hashes[relative] = revision["sha256"]
        except (OSError, UnicodeError, ValueError):
            errors.append("Public revision is invalid or does not bind the original export hash.")
    for relative, record in value.get("exported_files", {}).items():
        path = ROOT.joinpath(*PurePosixPath(relative).parts)
        expected_hash = revised_hashes.get(relative, record.get("sha256"))
        if not path.is_file() or _sha256(path) != expected_hash:
            errors.append(f"Exported provenance hash mismatch: {relative}")
    if after_demo:
        for relative, record in value.get("protected_inputs", {}).items():
            path = ROOT.joinpath(*PurePosixPath(relative).parts)
            if not path.exists():
                errors.append(f"Protected public path is missing: {relative}")
                continue
            if relative == "pbit/pbi_trial.pbit":
                continue
            actual = _tree_sha256(path) if path.is_dir() else _sha256(path)
            if actual != record.get("source_sha256"):
                errors.append(f"Protected public input changed: {relative}")


def _check_private_editorial(errors: list[str]) -> None:
    """Ignore only local drafts; ignore rules cannot excuse tracked private content."""
    paths = [*PRIVATE_EDITORIAL, ".tmp/publication-private/"]
    try:
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *paths],
                                 capture_output=True, check=False)
    except OSError:
        tracked = None
    if tracked is not None and tracked.returncode == 0:
        for name in tracked.stdout.decode("utf-8").split("\0"):
            if name:
                errors.append(f"Private editorial file is tracked or staged: {name}")
    for relative in PRIVATE_EDITORIAL:
        if not (ROOT / relative).exists():
            continue
        if tracked is None or tracked.returncode != 0:
            errors.append(f"Private editorial file present without Git ignore verification: {relative}")
            continue
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--", relative],
                                 capture_output=True, check=False)
        if ignored.returncode != 0:
            errors.append(f"Private editorial file is not ignored: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-demo", action="store_true")
    args = parser.parse_args()
    errors: list[str] = []
    _check_private_editorial(errors)
    required = {
        "README.md", "LICENSE", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
        "pyproject.toml", "PUBLIC_PROVENANCE.json",
        "docs/EVIDENCE.md", "docs/LIMITATIONS.md", "docs/DEMO.md",
        "docs/images/agent-review-flow.png", "docs/images/agent-review-flow.svg",
        "docs/images/conversion-funnel.svg", "docs/images/relationship-comparison.svg",
        "demo/run_public_demo.py",
    }
    paths = {
        item.relative_to(ROOT).as_posix()
        for item in ROOT.rglob("*")
        if item.is_file()
    }
    for missing in sorted(required - paths):
        errors.append(f"Required public file is missing: {missing}")
    for relative in sorted(paths):
        if _ignored_runtime(relative):
            continue
        path = ROOT.joinpath(*PurePosixPath(relative).parts)
        if any(part in FORBIDDEN_PATH_PARTS for part in PurePosixPath(relative).parts):
            errors.append(f"Forbidden public path: {relative}")
        if path.name == "profiles.yml":
            errors.append(f"Forbidden public path: {relative}")
        if path.stat().st_size > 5 * 1024 * 1024:
            errors.append(f"File exceeds 5 MiB: {relative}")
        _scan_text(path, errors)
    readme_words = len(re.findall(r"\b[\w'-]+\b", (ROOT / "README.md").read_text(encoding="utf-8")))
    if readme_words > 1500:
        errors.append(f"README exceeds 1,500 words: {readme_words}")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if metadata.get("project", {}).get("license") != "Apache-2.0":
        errors.append("Package metadata does not declare Apache-2.0.")
    _check_links(errors)
    _check_svg(errors)
    _check_pbit(errors)
    _check_provenance(errors, after_demo=args.after_demo)
    if errors:
        for error in errors:
            print(f"PUBLICATION_CHECK_FAILED: {error}")
        return 1
    print("PUBLIC_REPOSITORY_CHECK_ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

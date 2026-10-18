"""Narrow public revisions must not weaken historical export integrity checks."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


CHECKER_PATH = Path(__file__).resolve().parents[1] / "scripts/check_public_repository.py"
spec = importlib.util.spec_from_file_location("readme_revision_checker", CHECKER_PATH)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def public_tree(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    readme = "Original public README.\n"
    other = "select 'original export';\n"
    checker_source = "print('original checker')\n"
    (tmp_path / "README.md").write_bytes(readme.encode("utf-8"))
    (tmp_path / "docs/exported.sql").write_bytes(other.encode("utf-8"))
    (tmp_path / "scripts/check_public_repository.py").write_bytes(checker_source.encode("utf-8"))
    provenance = {
        "schema_version": 1,
        "private_source_commit": "0" * 40,
        "private_source_dirty": False,
        "local_review_only": False,
        "exported_files": {
            "README.md": {"sha256": digest(readme)},
            "docs/exported.sql": {"sha256": digest(other)},
            "scripts/check_public_repository.py": {"sha256": digest(checker_source)},
        },
    }
    (tmp_path / "PUBLIC_PROVENANCE.json").write_text(json.dumps(provenance), encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    return tmp_path, provenance


def reviewed_revision(public_tree):
    root, provenance = public_tree
    revised = "Original public README.\n\nApproved teaching section.\n"
    (root / "README.md").write_bytes(revised.encode("utf-8"))
    revision = {
        "base_sha256": provenance["exported_files"]["README.md"]["sha256"],
        "sha256": digest(revised),
        "reason": "Approved additive teaching documentation.",
    }
    (root / "docs/PUBLIC_REVISIONS.json").write_text(json.dumps({"README.md": revision}), encoding="utf-8")
    return root, revision


def check_errors():
    errors = []
    checker._check_provenance(errors, after_demo=False)
    return errors


def test_original_export_still_passes_without_a_revision(public_tree):
    assert check_errors() == []


def test_revision_binds_the_original_export_and_current_readme(public_tree):
    reviewed_revision(public_tree)
    assert check_errors() == []


def test_revision_with_wrong_base_hash_is_rejected(public_tree):
    root, revision = reviewed_revision(public_tree)
    revision["base_sha256"] = "f" * 64
    (root / "docs/PUBLIC_REVISIONS.json").write_text(json.dumps({"README.md": revision}), encoding="utf-8")
    assert "Public revision is invalid or does not bind the original export hash." in check_errors()


def test_changed_readme_must_match_the_reviewed_current_hash(public_tree):
    root, _revision = reviewed_revision(public_tree)
    (root / "README.md").write_text("Unreviewed replacement.\n", encoding="utf-8")
    assert "Exported provenance hash mismatch: README.md" in check_errors()


def test_readme_revision_does_not_authorize_other_export_changes(public_tree):
    root, _revision = reviewed_revision(public_tree)
    (root / "docs/exported.sql").write_text("select 'changed export';\n", encoding="utf-8")
    assert check_errors() == ["Exported provenance hash mismatch: docs/exported.sql"]


def test_checker_revision_requires_its_own_exact_base_and_current_hashes(public_tree):
    root, readme_revision = reviewed_revision(public_tree)
    provenance = public_tree[1]
    source = "print('reviewed checker')\n"
    (root / "scripts/check_public_repository.py").write_bytes(source.encode("utf-8"))
    revisions = {
        "README.md": readme_revision,
        "scripts/check_public_repository.py": {
            "base_sha256": provenance["exported_files"]["scripts/check_public_repository.py"]["sha256"],
            "sha256": digest(source),
            "reason": "Reviewed narrow provenance validation extension.",
        },
    }
    (root / "docs/PUBLIC_REVISIONS.json").write_text(json.dumps(revisions), encoding="utf-8")
    assert check_errors() == []
    (root / "scripts/check_public_repository.py").write_bytes(b"print('unreviewed checker')\n")
    assert check_errors() == ["Exported provenance hash mismatch: scripts/check_public_repository.py"]


def test_revision_map_cannot_override_an_unrelated_exported_path(public_tree):
    root, readme_revision = reviewed_revision(public_tree)
    provenance = public_tree[1]
    changed = "select 'changed export';\n"
    (root / "docs/exported.sql").write_bytes(changed.encode("utf-8"))
    revisions = {
        "README.md": readme_revision,
        "docs/exported.sql": {
            "base_sha256": provenance["exported_files"]["docs/exported.sql"]["sha256"],
            "sha256": digest(changed),
            "reason": "This unrelated path is not an allowed revision target.",
        },
    }
    (root / "docs/PUBLIC_REVISIONS.json").write_text(json.dumps(revisions), encoding="utf-8")
    errors = check_errors()
    assert "Public revision is invalid or does not bind the original export hash." in errors
    assert "Exported provenance hash mismatch: docs/exported.sql" in errors


@pytest.mark.parametrize("malformation", ["invalid_json", "array", "invalid_hash", "empty_reason", "numeric_reason"])
def test_malformed_revision_fails_closed(public_tree, malformation):
    root, revision = reviewed_revision(public_tree)
    if malformation == "invalid_json":
        payload = "{"
    elif malformation == "array":
        payload = "[]"
    else:
        if malformation == "invalid_hash":
            revision["sha256"] = "not-a-sha256"
        elif malformation == "empty_reason":
            revision["reason"] = "  "
        else:
            revision["reason"] = 1
        payload = json.dumps({"README.md": revision})
    (root / "docs/PUBLIC_REVISIONS.json").write_text(payload, encoding="utf-8")
    errors = check_errors()
    assert "Public revision is invalid or does not bind the original export hash." in errors
    assert "Exported provenance hash mismatch: README.md" in errors


def test_historical_snapshot_links_use_the_original_document_location(public_tree):
    root, _provenance = public_tree
    snapshot = root / "labs/semantic_interop/results/task05-minimal/showcase-readme.before.md"
    target = root / "labs/semantic_interop/fixtures/conversion_demo/contract.json"
    snapshot.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    snapshot.write_text("[Canonical fixture](../fixtures/conversion_demo/contract.json)\n", encoding="utf-8")
    before = snapshot.read_bytes()
    errors = []
    checker._check_links(errors)
    assert errors == []
    assert snapshot.read_bytes() == before


def test_historical_snapshot_still_reports_a_missing_original_link(public_tree):
    root, _provenance = public_tree
    relative = "labs/semantic_interop/results/task05-minimal/showcase-readme.before.md"
    snapshot = root / relative
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("[Missing evidence](../results/missing.json)\n", encoding="utf-8")
    errors = []
    checker._check_links(errors)
    assert errors == [f"Broken local link in {relative}: ../results/missing.json"]


def test_unrecognized_snapshot_resolves_links_from_its_own_directory(public_tree):
    root, _provenance = public_tree
    relative = "docs/showcase-readme.before.md"
    snapshot = root / relative
    snapshot.write_text("[Local file](exported.sql)\n[Missing file](missing.sql)\n", encoding="utf-8")
    errors = []
    checker._check_links(errors)
    assert errors == [f"Broken local link in {relative}: missing.sql"]


def test_angle_bracket_destination_accepts_a_local_path_with_spaces(public_tree):
    root, _provenance = public_tree
    (root / "docs/Sales Channel.pq").write_text("let Source = 1 in Source\n", encoding="utf-8")
    (root / "docs/links.md").write_text("[Query](<Sales Channel.pq#fragment>)\n", encoding="utf-8")
    errors = []
    checker._check_links(errors)
    assert errors == []


def test_angle_bracket_destination_still_reports_a_missing_path(public_tree):
    root, _provenance = public_tree
    (root / "docs/links.md").write_text("[Missing query](<Missing File.pq>)\n", encoding="utf-8")
    errors = []
    checker._check_links(errors)
    assert errors == ["Broken local link in docs/links.md: <Missing File.pq>"]


def test_angle_bracket_destination_cannot_escape_the_repository(public_tree):
    root, _provenance = public_tree
    outside = root.parent / f"{root.name}-outside file.txt"
    outside.write_text("Outside the publication root.\n", encoding="utf-8")
    destination = f"../../{outside.name}"
    (root / "docs/links.md").write_text(f"[Outside file](<{destination}>)\n", encoding="utf-8")
    errors = []
    checker._check_links(errors)
    assert errors == [f"Broken local link in docs/links.md: <{destination}>"]

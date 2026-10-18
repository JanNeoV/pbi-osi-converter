"""Protect the evidence boundary of the Databricks showcase orchestration."""
from pathlib import Path

import yaml

from semantic_lab.showcase_databricks import NATIVE, _leg, compare_native_definitions


def test_native_comparison_allows_only_whole_source_trailing_whitespace(tmp_path):
    definition = yaml.safe_load(NATIVE.read_text(encoding="utf-8"))
    definition["source"] = definition["source"].rstrip()
    target = tmp_path / "returned.yml"
    target.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = compare_native_definitions(NATIVE, target)
    assert result["parsed_yaml_equal"] is False
    assert result["definitions_equal_ignoring_source_sql_trailing_whitespace"] is True
    assert result["differing_top_level_keys"] == ["source"]


def test_native_comparison_detects_lost_eligibility_filter(tmp_path):
    definition = yaml.safe_load(NATIVE.read_text(encoding="utf-8"))
    del definition["filter"]
    target = tmp_path / "lost-filter.yml"
    target.write_text(yaml.safe_dump(definition), encoding="utf-8")
    assert compare_native_definitions(NATIVE, target)["definitions_equal_ignoring_source_sql_trailing_whitespace"] is False


def test_unknown_jar_cannot_produce_a_converter_pass(tmp_path, monkeypatch):
    def forbidden_run(*args, **kwargs):
        raise AssertionError("Unverified converter must not execute")

    monkeypatch.setattr("semantic_lab.showcase_databricks.subprocess.run", forbidden_run)
    result = _leg(tmp_path, "export", tmp_path / "source.yml", tmp_path / "target.yml",
                  Path("java"), tmp_path / "unverified.jar", "export", official_build_verified=False)
    assert result["conversion"] == "NOT_EXECUTED"
    assert result["command"]["exit_code"] is None
    assert (tmp_path / "export.command.json").is_file()
    assert (tmp_path / "export.stderr.log").read_bytes() == b""

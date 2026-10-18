"""Minimal local upstream candidate: an unmodified round trip loses required configuration."""
from pathlib import Path
from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from ossie_dbt import MSIToOssieConverter, OssieToMSIConverter

fixture = Path(__file__).resolve().parents[1] / "results/evidence/source.manifest.normalized.json"
source = parse_manifest_from_dbt_generated_manifest(fixture.read_text())
SemanticManifestLookup(source)
outward = MSIToOssieConverter().convert(source)
inward = OssieToMSIConverter().convert(outward.output)
print("Source time spines:", len(source.project_configuration.time_spines))
print("Returned time spines:", len(inward.output.project_configuration.time_spines))
print("Converter issues:", len(outward.issues) + len(inward.issues))
try:
    SemanticManifestLookup(inward.output)
except Exception as exc:
    print(type(exc).__name__ + ": " + str(exc))
else:
    raise SystemExit("The recorded limitation no longer reproduces; inspect versions and refresh evidence.")

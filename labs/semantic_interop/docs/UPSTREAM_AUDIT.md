# Upstream audit and actual integrations

Inspected on 2026-10-16. Source references use the tested commit/version rather
than assuming today's main branch remains compatible.

| Tool | Pinned version | Verification |
|---|---|---|
| Python | 3.12; local 3.12.10 | Local Windows execution |
| dbt Core | 1.12.6 | Builds four synthetic views and parses the semantic manifest |
| dbt-duckdb | 1.11.0 | Real local adapter configuration |
| dbt-metricflow | 0.15.0 | Official adapter-backed client |
| MetricFlow | 0.213.0 | Compiles all 14 native requests; emitted SQL executes in DuckDB |
| DuckDB | 1.5.6 | Independent oracle and generated SQL execution |
| Ossie core/dbt/Microsoft | 0.2.0.dev0 | Source commit `6ebc5e5612467dac405985f44ea078b39c891bee`; real converters and core Pydantic model validation |
| pytest | 8.4.2 | Lab and existing repository tests |

The complete dependency graph is in the frozen lockfile and actual installed
versions are in each report. The dependency resolver and package builds succeeded.
No stable Ossie GitHub release or published `apache-ossie*` PyPI packages were found
during the audit; the README's package-install examples are not treated as release
evidence. The available tag was a release candidate. Source pins explicitly identify
the development snapshot used here.

## dbt and MetricFlow

[MetricFlow 0.213.0](https://github.com/dbt-labs/metricflow/releases/tag/v0.213.0)
provides the compiler used here. The
[dbt documentation](https://docs.getdbt.com/docs/build/about-metricflow) separates
metric definitions, query compilation and the hosted Semantic Layer. This lab uses
the local compiler and DuckDB adapter, not a hosted Semantic Layer account.

The fixture uses dbt semantic-model/measure YAML accepted by the pinned parser.
It is authored independently from the SQL oracle. `MetricFlowEngine.explain` returns
SQL using the official adapter's renderer. DuckDB executes that SQL directly to keep
the comparison boundary explicit. The adapter handles no cloud connection.

The official [Ossie dbt converter](https://github.com/apache/ossie/tree/6ebc5e5612467dac405985f44ea078b39c891bee/converters/dbt)
supports MSI to Ossie and Ossie to MSI. Documented losses include cumulative windows,
conversion/private metrics and natural entities. The converter cannot be inferred
to preserve arbitrary dbt semantics from that direction-level support.

Observed on this fixture: both conversion calls succeed with zero converter issues.
The returned manifest has zero time spines versus one in the source. Constructing
`SemanticManifestLookup` on it raises `SemanticManifestConfigurationError` before
SQL compilation. Returned models also lack entity declarations and default time
dimensions. A completed-revenue definition becomes an aggregate expression assigned
to the orders semantic model, instead of an item-model sum with a metric filter.
These are static differences, not measured numerical round-trip errors.

Run `python labs/semantic_interop/scripts/reproduce_roundtrip.py` from the repository
root with the lab environment to reproduce the configuration loss using only the
captured source manifest and official libraries. No dbt build or connection is needed.

## Microsoft and Power BI

The [official Ossie Microsoft converter](https://github.com/apache/ossie/tree/6ebc5e5612467dac405985f44ea078b39c891bee/converters/microsoft)
supports offline TMSL import/export and a narrow SQL-to-DAX subset. DAX expressions
are preserved as DAX, not translated into executable DuckDB SQL. Unsupported SQL
becomes an annotated `BLANK()` with warnings.

On the generated Ossie model, order count, completed revenue, AOV and completed share
become placeholders. Two relationship endpoints refer to names absent from the
exported columns and are skipped. Revenue and purchasing-customer expressions are
emitted, but their numerical behavior is not established. The returned Ossie document
passes the official Pydantic model validation; this does not undo the lost semantics.
The TMSL includes placeholder Direct Lake source IDs and is not a load-ready report.

[Microsoft TMDL documentation](https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview)
describes TOM serialization and folder-based definitions.
[PBIP documentation](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview)
does not establish native Ossie import. The local authored TMDL fixture is inspected
with existing repository code; it is not presented as output of an official converter.

### Optional TOM follow-up

TOM is intentionally absent from the mandatory environment. No .NET runtime was
available on PATH during implementation. Upstream's optional `tom` extra pins
`clr-loader==0.3.1` and `pythonnet==3.1.0`; its restore script pins the Microsoft
Analysis Services NuGet assemblies for .NET 8. Use that script from the same Ossie
commit in a separate optional environment after reviewing Microsoft's license.
Then run `validate_bim` or the official TMDL serialization APIs on copied artifacts.

Offline TOM validates model structure and references, not DAX syntax or numerical
results. Upstream's live `validate_with_engine` creates Fabric items and consumes
capacity; it is outside this milestone and is never called by the lab.

## Contribution assessment

A focused upstream regression case and documentation clarification are justified:
the advertised round-trip manifest is not immediately loadable by the pinned current
MetricFlow, and the converter reports no issue for this loss. The included reproducer
is sufficient to discuss project-configuration preservation or an explicit diagnostic.
The entity/field alias mismatch is a second candidate requiring upstream review.
No patch, issue, PR or general correctness claim has been submitted.

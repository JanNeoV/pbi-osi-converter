# Charlotte's Databricks handoff

This is a two-metric continuation of Task 03: **Conversion Rate** and **Paid
Revenue**. Local DuckDB/MetricFlow results are measured; Databricks creation,
analysis and numerical execution remain `NOT_EXECUTED`. No credentials are assumed.
Start with the [five-minute walkthrough](CONVERSION_DEMO.md), then use this file
for the Databricks lane. Keep generated artifacts and any manual continuation in
different files and report them as different experiments.

## Inputs and business contract

All paths below are relative to the repository root. The only business fixture is
`labs/semantic_interop/fixtures/conversion_demo/`:

| Input | Exact path |
|---|---|
| 12 session rows | `labs/semantic_interop/fixtures/conversion_demo/data/sessions.csv` |
| Six order rows | `labs/semantic_interop/fixtures/conversion_demo/data/orders.csv` |
| Contract | `labs/semantic_interop/fixtures/conversion_demo/contract.json` |
| Authored MetricFlow source | `labs/semantic_interop/fixtures/conversion_demo/dbt/models/metrics.yml` |
| Source SQL | `labs/semantic_interop/fixtures/conversion_demo/dbt/models/sessions.sql`, `labs/semantic_interop/fixtures/conversion_demo/dbt/models/orders.sql` |
| Independent oracles | `labs/semantic_interop/fixtures/conversion_demo/oracle/conversion_rate.sql`, `labs/semantic_interop/fixtures/conversion_demo/oracle/paid_revenue.sql` |
| Fresh manifest | `labs/semantic_interop/results/task04/metricflow/source.manifest.json` |
| Actual Ossie output | `labs/semantic_interop/results/task04/metricflow/platforms/converted.ossie.yml` |
| Generated Databricks evidence | `labs/semantic_interop/results/task04/databricks/` |
| Manually authored Metric View | `labs/semantic_interop/fixtures/conversion_demo/targets/databricks/metric_view.yaml` |
| Prospective target queries | `labs/semantic_interop/fixtures/conversion_demo/targets/databricks/validate.sql` |

Use explicit types: IDs `BIGINT`; dates `DATE`; channel/status/exclusion reason
`STRING`; eligibility `BOOLEAN`; amount `DECIMAL(18,2)`. Include sessions starting
`2026-01-01 <= session_date < 2026-03-01`; observe only orders with
`order_date < 2026-03-01`. A converted session is an eligible session with at least
one paid order before that cutoff. Revenue sums paid order amounts from eligible
originating sessions. Preserve sessions without orders. Recompute the ratio from
distinct session counts at every requested grain; never average channel rates.
Empty monetary sums and zero-denominator ratios are NULL. A positive denominator
with no conversions returns 0%. Status is a whole-order snapshot, with no partial
refunds, tax, currency conversion, or event history.

The January 6 session has an EUR 80 paid order on February 1. Both its conversion
and revenue belong to the **January session cohort**, because session date defines
the reporting cohort and order date only applies the observation cutoff. January
and February cohort revenues are EUR 270 and EUR 120. Grouping the same orders by
transaction month would instead produce EUR 190 and EUR 200; the equal EUR 390
total does not make those two questions equivalent.

| Session cohort/filter | Conversion Rate | Paid Revenue (EUR) |
|---|---:|---:|
| Overall | 40% | 390.00 |
| Direct | 50% | 300.00 |
| Indirect | 25% | 90.00 |
| January | 60% | 270.00 |
| February | 20% | 120.00 |
| January Direct | 66.666…% | 180.00 |
| January Indirect | 50% | 90.00 |
| February Direct | 33.333…% | 120.00 |
| February Indirect | 0% | NULL |
| Empty cohort | NULL | NULL |

Supporting counts, AOV and the separate repeated-session edge are internal
validation cases. The canonical counts are 10 eligible sessions, four converted
sessions, and four paid orders. Do not load `additional_order.csv` into this run.

## Official directions and frozen versions

Checked against official sources on **2026-10-18**. The baseline environment stays
unchanged: Ossie core/dbt/Microsoft `0.2.0.dev0`, commit
`6ebc5e5612467dac405985f44ea078b39c891bee`; MetricFlow `0.213.0`;
dbt Core `1.12.6`; dbt-duckdb `1.11.0`; DuckDB `1.5.6`. The later Task 02
upstream replay is commit `ef91cb61092c60f4b918016a81cfdbe483e113bc` and is separate.

The current official Databricks adapter is Java, version **`0.1.0-SNAPSHOT`**, pinned
here to current-main commit **`53418ba8a4b8a20951334fdb9d9638aa34b514e3`**.
Its Java module has no source diff from the Task 02 replay commit. It supports
**Ossie → Metric View (`export`)** and **Metric View → Ossie (`import`)** offline.
The official dbt adapter supports **MetricFlow semantic manifest ↔ Ossie**; the
Microsoft adapter supports **Power BI TMSL/model.bim ↔ Ossie**. Direction support
is not a promise of semantic or numerical equivalence. See the
[official Databricks adapter README](https://github.com/apache/ossie/blob/53418ba8a4b8a20951334fdb9d9638aa34b514e3/converters/databricks/java/README.md)
and [version audit](../results/task04/upstream-review/review.json).

Java **21+** and Maven **3.6+** are required to build the self-contained JAR.
The offline converter itself needs Java and the built JAR, with no Databricks
account. First Maven build requires access to Maven Central. Use a separate
checkout/build directory and a private Maven cache; do not upgrade the Python lab.

From PowerShell at the repository root, with Java/Maven already installed:

```powershell
$ErrorActionPreference = 'Continue' # Windows PowerShell treats native stderr as ErrorRecord.
java --version
mvn -version
$buildLogs = '.tmp/charlotte-build-logs'
New-Item -ItemType Directory -Path $buildLogs -ErrorAction Stop | Out-Null
git clone --no-checkout https://github.com/apache/ossie.git .tmp/charlotte-ossie
if ($LASTEXITCODE -ne 0) { throw 'Clone failed; use fresh directory names' }
git -C .tmp/charlotte-ossie checkout --detach 53418ba8a4b8a20951334fdb9d9638aa34b514e3
if ($LASTEXITCODE -ne 0) { throw 'Pinned checkout failed' }
$checkedOut = git -C .tmp/charlotte-ossie rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $checkedOut -ne '53418ba8a4b8a20951334fdb9d9638aa34b514e3') { throw 'Wrong upstream commit' }
$checkedOut | Set-Content "$buildLogs/upstream-commit.log"
git -C .tmp/charlotte-ossie status --short
if (Test-Path -LiteralPath .tmp/charlotte-databricks-build) { throw 'Build destination already exists' }
Copy-Item -LiteralPath .tmp/charlotte-ossie/converters/databricks/java -Destination .tmp/charlotte-databricks-build -Recurse
$mavenCache = Join-Path (Get-Location) '.tmp/charlotte-maven-cache'
mvn -B -f .tmp/charlotte-databricks-build/pom.xml "-Dmaven.repo.local=$mavenCache" clean package 1> "$buildLogs/build.stdout.log" 2> "$buildLogs/build.stderr.log"
if ($LASTEXITCODE -ne 0) { throw 'Official adapter build failed' }
$jar = '.tmp/charlotte-databricks-build/target/ossie-databricks-converter-0.1.0-SNAPSHOT.jar'
$out = 'labs/semantic_interop/results/task04/charlotte-databricks-NEW'
New-Item -ItemType Directory -Path $out -ErrorAction Stop | Out-Null
$inputOssie = 'labs/semantic_interop/results/task04/metricflow/platforms/converted.ossie.yml'
Copy-Item -LiteralPath $inputOssie -Destination "$out/input.ossie.yml"
java -jar $jar export "$out/input.ossie.yml" -o "$out/generated.metric-view.yml" --source sessions 1> "$out/export.stdout.log" 2> "$out/export.stderr.log"
$exportExit = $LASTEXITCODE
"export_exit=$exportExit" | Set-Content "$out/exit-codes.log"
if ($exportExit -ne 0) { throw 'Preserve failure logs; do not substitute an authored view' }
java -jar $jar import "$out/generated.metric-view.yml" -o "$out/reimported.ossie.yml" --name conversion_demo_databricks 1> "$out/import.stdout.log" 2> "$out/import.stderr.log"
$importExit = $LASTEXITCODE
"import_exit=$importExit" | Add-Content "$out/exit-codes.log"
if ($importExit -ne 0) { throw 'Preserve re-import failure logs' }
Get-FileHash -Algorithm SHA256 $jar,"$out/input.ossie.yml","$out/generated.metric-view.yml","$out/reimported.ossie.yml"
git -C .tmp/charlotte-ossie status --short
```

Use fresh directory names on every repeat. The tested build, commands, notices,
input/JAR/output hashes and local structural observations are retained in
[`databricks/report.json`](../results/task04/databricks/report.json). Original
converter output is never edited to make it execute. The measured build passed
**100 upstream tests**. Export and re-import both exited 0; the output parses as
YAML and declares Metric View specification version 1.1, with five dimensions,
nine measures and a native `one_to_many` orders
join from sessions. Export emitted **12 notices**, retained verbatim: seven
one-to-many fields dropped, three time-dimension markers dropped, and two notices
about key metadata. All nine measures still contain the unresolved
`session__is_eligible` token; paid measures also contain `order__order_status`.
Those are not columns of the canonical source views. Databricks creation/analyzer
validation and numerical execution are `NOT_EXECUTED`; successful YAML parsing
and re-import only establish that these tools accepted the text. The returned
Ossie also passes official core Pydantic validation. The generated dimensions
include `session_date`, but no derived `session_month`; adding that dimension for
the cohort queries is another explicit intervention.

## Databricks prerequisites and execution

The [official feature matrix](https://docs.databricks.com/aws/en/uc-semantics/metric-views/feature-availability)
distinguishes **YAML specification 1.1** from engine versions: metric views start
at DBR 16.4, comments/metadata need 17.3, and native `one_to_many` joins need
**18.1+**. SQL warehouses receive Databricks SQL compute updates automatically;
record warehouse channel/version and verify feature support. Use Unity Catalog
and an authorized catalog/schema. The setup below needs `USE CATALOG`,
`USE SCHEMA`, schema `CREATE TABLE`, source `SELECT`, and `READ VOLUME`;
uploading also needs `WRITE VOLUME` or an authorized equivalent upload method.
View creation uses the schema's `CREATE TABLE` privilege; see the official
[CREATE VIEW requirements](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-view).
The [YAML reference](https://docs.databricks.com/aws/en/uc-semantics/metric-views/yaml-reference)
accepts SQL-query sources, aggregate measures, `filter`, and `dimensions` as an
alias for the preferred `fields` keyword. Metric Views are created with
[`WITH METRICS LANGUAGE YAML`](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-view)
and queried with `MEASURE(metric_name)`.

Upload the exact two CSVs to an authorized Unity Catalog volume. Then run this
prospective notebook setup with your own catalog/schema/volume; it has not been
executed here. This Python block needs Spark notebook compute exposing `spark`
(for example, an authorized serverless notebook); a SQL warehouse can run the
subsequent SQL but cannot run this ingestion block. Use an empty, dedicated
schema so `errorifexists` protects prior
data. Preserve file hashes before upload and table row counts after loading.

```python
catalog, schema = 'YOUR_CATALOG', 'YOUR_EMPTY_SCHEMA'
volume_path = '/Volumes/YOUR_CATALOG/YOUR_SCHEMA/YOUR_VOLUME/conversion_demo'
assert catalog.replace('_', '').isalnum() and schema.replace('_', '').isalnum()
prefix = f'`{catalog}`.`{schema}`'
session_schema = 'session_id BIGINT, session_date DATE, sales_channel STRING, is_eligible BOOLEAN, exclusion_reason STRING'
order_schema = 'order_id BIGINT, session_id BIGINT, order_date DATE, amount_eur DECIMAL(18,2), order_status STRING'
for filename, table, types in [('sessions.csv', 'raw_sessions', session_schema), ('orders.csv', 'raw_orders', order_schema)]:
    frame = spark.read.schema(types).option('header', True).option('mode', 'FAILFAST').csv(f'{volume_path}/{filename}')
    frame.write.format('delta').mode('errorifexists').saveAsTable(f'{prefix}.`{table}`')
assert spark.table(f'{prefix}.raw_sessions').count() == 12
assert spark.table(f'{prefix}.raw_orders').count() == 6
spark.sql(f"CREATE VIEW {prefix}.sessions AS SELECT * FROM {prefix}.raw_sessions WHERE session_date >= DATE '2026-01-01' AND session_date < DATE '2026-03-01'")
spark.sql(f"CREATE VIEW {prefix}.orders AS SELECT o.*, s.session_date FROM {prefix}.raw_orders o JOIN {prefix}.raw_sessions s ON o.session_id=s.session_id WHERE o.order_date < DATE '2026-03-01'")
```

For the **generated lane**, first inspect `generated.metric-view.yml` and the
recorded blockers. It references the dbt source views, rather than raw CSVs.
Catalog bindings, unresolved tokens, and join constraints must be reviewed before
creation. Preserve the original, save a separately named working copy, and record
every edit/diff (including namespace/identifier changes) as an intervention. A
manually rebound/repaired generated view is a separate result from unchanged
official conversion. Do not claim its numerical result for the unchanged lane.
For a generated continuation, explicitly record namespace binding, token
resolution, addition of `session_month`, and review/rework of aggregates whose
predicates combine session and order columns. Each aggregate in a native
one-to-many model must reference only one source. Keep its resulting file and
analyzer output separate from the original and the authored candidate.
To attempt unchanged generated creation, use the `CREATE VIEW ... WITH METRICS
LANGUAGE YAML AS $$ ... $$` wrapper below with view name
`conversion_metrics_generated` and the raw generated YAML. Retain the actual
creation error if it fails. Use `conversion_metrics_rebound` for any edited copy.

For the **authored lane**, copy `targets/databricks/metric_view.yaml`, replace
`demo.semantic_interop` with the authorized namespace, and call the target
`conversion_metrics_authored`. This is a manually authored sessions-preserving
SQL source with the same business contract. Its prospective runtime is also
`NOT_EXECUTED`. In a Databricks SQL editor, wrap the chosen YAML verbatim:

```sql
CREATE VIEW YOUR_CATALOG.YOUR_SCHEMA.conversion_metrics_authored
WITH METRICS LANGUAGE YAML AS $$
-- Paste the separately retained, namespace-bound authored YAML here.
$$;
SELECT MEASURE(conversion_rate), MEASURE(paid_revenue)
FROM YOUR_CATALOG.YOUR_SCHEMA.conversion_metrics_authored;
SELECT sales_channel, MEASURE(conversion_rate), MEASURE(paid_revenue)
FROM YOUR_CATALOG.YOUR_SCHEMA.conversion_metrics_authored
GROUP BY sales_channel ORDER BY sales_channel;
SELECT session_month, MEASURE(conversion_rate), MEASURE(paid_revenue)
FROM YOUR_CATALOG.YOUR_SCHEMA.conversion_metrics_authored
GROUP BY session_month ORDER BY session_month;
```

Run the remaining internal cases in `validate.sql`, changing only the view name
and namespace. Compare actual typed rows with the independent `oracle_result`
records in `results/task04/metricflow/report.json`. Keep DATE and DECIMAL types,
NULLs, missing rows and empty groups distinct; percentage display is not the
stored ratio. Preserve raw query SQL, result exports, errors and `SHOW CREATE
TABLE`/view definition. Runtime errors are blockers, not numerical mismatches.

## The grain decision Charlotte must preserve

The authored SQL source starts at sessions and LEFT JOINs qualifying orders. It
retains non-buying sessions and uses distinct session counts to tolerate multiple
orders, while order-level amounts remain additive. It is transparent and works
without native one-to-many feature support, but the author must control fanout
and cutoff placement. A native sessions-source `one_to_many` orders join can
aggregate each side at its own grain on DBR 18.1+; Databricks restricts joined
one-to-many columns in fields and each aggregate must reference one source.
The official exporter defaults to the FK-sink (orders): switching to that default
would lose sessions without orders from the denominator. This handoff therefore
passes **`--source sessions`** explicitly. It does not certify that the resulting
expressions satisfy all Databricks restrictions. Keep session-month attribution
and test the zero-conversion cohort before considering any changed representation.

## Capture template and troubleshooting

Create a new results directory and fill this template once per lane:

```json
{
  "lane": "UNCHANGED_GENERATED | MANUALLY_REBOUND_GENERATED | AUTHORED",
  "input_csv_sha256": {}, "source_model_sha256": "", "target_yaml_sha256": "",
  "converter_commit": "53418ba8a4b8a20951334fdb9d9638aa34b514e3",
  "jar_sha256": "", "java_version": "", "maven_version": "",
  "runtime_or_warehouse_version_and_channel": "", "namespace": "",
  "interventions_and_diff": [], "warnings_and_errors": [],
  "conversion_completed": null, "structure_validated": null,
  "semantics_preserved": null, "numerical_execution_completed": false,
  "oracle_comparison_passed": null,
  "cases": [{"case_id": "conversion_rate__overall", "sql_path": "",
    "target_result_path": "", "oracle_result_path": "",
    "execution_status": "NOT_EXECUTED", "comparison": null}],
  "evidence_files_and_sha256": {}
}
```

- Missing `java`/`mvn`, wrong Java release, or Maven resolution failure: install
  Java 21+ and Maven 3.6+ separately, check `JAVA_HOME`, and retain the complete
  build log. Do not alter the frozen Python environment or adapter implementation.
- Export failure: retain stderr and exit code; check three-part sources, chosen
  source dataset, supported expression dialects and relationship graph. A failed
  export has no generated target to validate.
- Unresolved `session__is_eligible`/`order__order_status` or missing `amount_eur`:
  inspect the retained source Ossie and raw output; these can be upstream model
  translation problems. Both unresolved tokens are present in this measured
  generated file. `amount_eur` is a physical column of the canonical orders view
  although the Ossie field is named `revenue`. Do not silently replace tokens in
  accepted evidence.
- One-to-many field/aggregate error: check DBR 18.1+ and the official restrictions.
  Record creation/analyzer output. Do not switch to orders grain to obtain a pass.
- Namespace/permissions failure: bind only a copied working definition to the
  authorized Unity Catalog schema and record the edit. Keep generated bytes.
- Wrong cohort revenue: check `session_date`, the exclusive cutoff and paid status.
  EUR 190/200 signals transaction-month grouping instead of EUR 270/120 cohorts.
- February Indirect missing or NULL conversion: verify sessions without paid orders
  remain visible. NULL revenue is expected there; conversion must be 0%.

Charlotte can reproduce offline conversion after provisioning Java/Maven. To run
the Databricks experiment she still needs an authorized workspace, Unity Catalog
schema/volume and privileges, compatible compute, uploaded canonical CSVs, and
review of the generated-model blockers. No Databricks runtime pass is claimed.

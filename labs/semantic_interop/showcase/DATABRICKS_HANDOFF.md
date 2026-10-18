# Charlotte's Databricks handoff

Investigate **MetricFlow → Apache Ossie → Databricks Metric View** on the same 12 sessions and six orders. The measured source returns **40% Conversion Rate and 390 EUR Paid Revenue**. Including the existing refunded order in revenue changes **390 → 440 EUR** while Conversion Rate stays **40%**.

Apache Ossie exchanges semantic definitions; the target engine must still execute them. MetricFlow matches the independent DuckDB oracle. The official cross-platform conversion produces Databricks YAML, but it retains unresolved MetricFlow identifiers. **Databricks creation, analyzer validation and queries remain NOT_EXECUTED. Numerical target equivalence is BLOCKED.** See the [nine-path results](RESULTS.md) and [first incompatible transformations](TASK05_DIAGNOSIS.md).

Your first experiment is the generated MetricFlow path. A manually authored Databricks view is a separate control. Its own native round trip is optional and cannot establish cross-platform correctness.

## Exact starting point on main

Repository: **<https://github.com/JanNeoV/pbi-osi-converter>**. Base branch: **merged `main`**. Record the actual checked-out `main` SHA before creating your contribution branch.

Run these onboarding commands from a directory where the new clone does not exist:

```powershell
git clone --branch main https://github.com/JanNeoV/pbi-osi-converter.git pbi-osi-converter-charlotte
if ($LASTEXITCODE -ne 0) { throw 'Clone failed' }
Set-Location pbi-osi-converter-charlotte
git rev-parse HEAD
git switch -c research/databricks-runtime-validation
if ($LASTEXITCODE -ne 0) { throw 'Branch creation failed' }
$runtime = '.tmp/charlotte-runtime-001'
New-Item -ItemType Directory -Path $runtime -ErrorAction Stop | Out-Null
git rev-parse HEAD | Set-Content "$runtime/repository-base-commit.txt"
git status --short | Set-Content "$runtime/repository-status.txt"
```

Include that base SHA in your result and PR. All remaining commands run from the repository root. Use fresh work/output directory names on repeats. Files under `.tmp/` are local working files; copy only reviewed, portable results into a new evidence directory for your contribution.

| Input or evidence | Repository path under `labs/semantic_interop/` |
|---|---|
| Canonical fixtures | `fixtures/conversion_demo/data/sessions.csv`, `orders.csv` |
| Business contract and independent SQL | `fixtures/conversion_demo/contract.json`; `fixtures/conversion_demo/oracle/` |
| Actual MetricFlow source | `fixtures/conversion_demo/dbt/models/metrics.yml` |
| Required source transformations | `fixtures/conversion_demo/dbt/models/sessions.sql`, `orders.sql` |
| Captured baseline MetricFlow → Ossie → Databricks | `results/task05-minimal/metricflow/baseline/output.ossie.yml`, `output.metric-view.yml`, `to-databricks.*` |
| Captured changed MetricFlow path | Same filenames in `results/task05-minimal/metricflow/include-refunded/` |
| Measured source results, baseline / changed | `results/task05-minimal/metricflow/{baseline,include-refunded}/source/execution.json` |
| Manually authored two-metric control | `showcase/databricks-task05/metric_view.baseline.yaml` |
| Control's exact-row typed SQL setup | `showcase/databricks-task05/setup.sql` |
| Native round-trip files, logs, comparisons, hashes | `results/task05-minimal/databricks/` |
| Earlier detailed conversion and runtime instructions | [Supporting Databricks guide](../docs/DATABRICKS_HANDOFF.md) |

Historical evidence is read-only. Task 02's repaired/reconstructed numerical results used an earlier fixture; they are not an unchanged-conversion success for this example. Task 03's [31 native cases](../docs/TASK03_VALIDATION.md), Task 04's [nine paths](../results/task04-showcase/paths.json), and Task 05's [validation](../results/task05-minimal/validation.json) remain separate results.

## Converter versions and local prerequisites

**Offline conversion** needs local Java and the official converter JAR. It does not need a Databricks account. A fresh environment/build initially downloads dependencies; execution after provisioning is offline. **Native Metric View execution** requires an authorized Databricks workspace and compatible compute.

Use the frozen versions, without upgrading adapters:

| Component | Pinned or measured version |
|---|---|
| Python setup | Python 3.12; uv `0.12.17` |
| Python Ossie core/dbt/Microsoft | `0.2.0.dev0`, upstream commit `6ebc5e5612467dac405985f44ea078b39c891bee` |
| MetricFlow / dbt Core / dbt-duckdb / DuckDB | `0.213.0` / `1.12.6` / `1.11.0` / `1.5.6` |
| Official Java Databricks adapter | `0.1.0-SNAPSHOT`, upstream commit `53418ba8a4b8a20951334fdb9d9638aa34b514e3` |
| Java module tree | `8309efe7fb695936e35e5f3ddab9ebace1925f63` |
| Historical build tools | Temurin `21.0.12.1+1`, Maven `3.9.11` |
| Historical measured JAR SHA-256 | `09af8c0868cd56eac6a2de0e07dd59c1288506d4dd5779c9db2d3fbcec1e490f` |

The [official pinned Java README](https://github.com/apache/ossie/blob/53418ba8a4b8a20951334fdb9d9638aa34b514e3/converters/databricks/java/README.md) requires Java 21+ and Maven 3.6+. Use Java 21 and Maven 3.9.11 to reproduce the measured toolchain; record your exact versions. The historical build passed 100 upstream tests. Task 05 verified the retained JAR/source hashes without rebuilding. Task 06 repeated the recipe below with a fresh checkout and empty Maven cache: **100 tests passed**, the new JAR matched the recorded hash, and its baseline export matched the captured YAML. See [publication validation](../docs/TASK06_VALIDATION.md). A new build must still report its own tests and hash. [Executable provenance](../results/task05-minimal/databricks/provenance.json) and [original build evidence](../results/task04/databricks/report.json) identify the historical artifact.

Install the Python environment and run existing offline tests first:

```powershell
python -m pip install uv==0.12.17
if ($LASTEXITCODE -ne 0) { throw 'uv installation failed' }
python labs/semantic_interop/scripts/setup.py
if ($LASTEXITCODE -ne 0) { throw 'Frozen setup failed' }
$py = '.tmp/semantic-lab-venv/Scripts/python.exe'
& $py -m pytest -q labs/semantic_interop/tests --basetemp .tmp/pytest-charlotte-001
if ($LASTEXITCODE -ne 0) { throw 'Resolve the offline test failure before target work' }
```

Build the separate official Java adapter. This recipe was executed with the provisioned Java/Maven versions above; the first build needs network access to GitHub and Maven Central. Keep all build output:

```powershell
$ErrorActionPreference = 'Continue' # Windows PowerShell can wrap native stderr.
$dbBuildLogs = '.tmp/charlotte-build-001'
New-Item -ItemType Directory -Path $dbBuildLogs -ErrorAction Stop | Out-Null
java -version 1> "$dbBuildLogs/java.stdout.log" 2> "$dbBuildLogs/java.stderr.log"
if ($LASTEXITCODE -ne 0) { throw 'Java 21 is required' }
mvn -version 1> "$dbBuildLogs/maven.stdout.log" 2> "$dbBuildLogs/maven.stderr.log"
if ($LASTEXITCODE -ne 0) { throw 'Maven is required' }
git clone --no-checkout https://github.com/apache/ossie.git .tmp/charlotte-ossie-001
if ($LASTEXITCODE -ne 0) { throw 'Clone failed; retain logs and use a fresh directory' }
git -C .tmp/charlotte-ossie-001 checkout --detach 53418ba8a4b8a20951334fdb9d9638aa34b514e3
if ($LASTEXITCODE -ne 0) { throw 'Pinned checkout failed' }
git -C .tmp/charlotte-ossie-001 rev-parse HEAD | Set-Content "$dbBuildLogs/upstream-commit.txt"
git -C .tmp/charlotte-ossie-001 status --short | Set-Content "$dbBuildLogs/source-status.txt"
$dbMavenCache = Join-Path (Get-Location) '.tmp/charlotte-maven-001'
mvn -B -f .tmp/charlotte-ossie-001/converters/databricks/java/pom.xml "-Dmaven.repo.local=$dbMavenCache" clean package 1> "$dbBuildLogs/build.stdout.log" 2> "$dbBuildLogs/build.stderr.log"
$dbBuildExit = $LASTEXITCODE
$dbBuildExit | Set-Content "$dbBuildLogs/build.exit-code.txt"
if ($dbBuildExit -ne 0) { throw 'Retain failed build logs' }
$dbJar = '.tmp/charlotte-ossie-001/converters/databricks/java/target/ossie-databricks-converter-0.1.0-SNAPSHOT.jar'
Get-FileHash -Algorithm SHA256 -LiteralPath $dbJar | Format-List | Out-File "$dbBuildLogs/jar-sha256.txt"
```

An existing verified build may be reused by setting `$dbJar` to its actual path. Record the path, source commit, Java version and JAR hash; do not assume another machine has the original `.tmp/task04-databricks/` directory.

## First experiment: MetricFlow → Ossie → Databricks

### Execute the source and both official conversions locally

The helper copies/loads the canonical fixtures and executes MetricFlow-generated SQL against DuckDB. It contains no converter implementation. These commands reproduce the baseline; converter stderr and exit codes remain visible in separate files:

```powershell
$mfWork = '.tmp/charlotte-metricflow-001'
$helper = 'labs/semantic_interop/showcase/metricflow_task05.py'
& $py $helper init --work $mfWork
if ($LASTEXITCODE -ne 0) { throw 'Fixture initialization failed' }
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$mfWork/dbt" --profiles-dir "$mfWork/dbt" 1> "$mfWork/build-baseline.stdout.log" 2> "$mfWork/build-baseline.stderr.log"
if ($LASTEXITCODE -ne 0) { throw 'dbt build failed' }
& $py $helper execute --work $mfWork --label baseline
if ($LASTEXITCODE -ne 0) { throw 'Source execution failed' }
& $py -m ossie_dbt.cli msi-to-ossie -i "$mfWork/dbt/target/semantic_manifest.json" -o "$mfWork/baseline.ossie.yml" --model-name conversion_demo 1> "$mfWork/baseline-to-ossie.stdout.log" 2> "$mfWork/baseline-to-ossie.stderr.log"
$LASTEXITCODE | Set-Content "$mfWork/baseline-to-ossie.exit-code.txt"
if ($LASTEXITCODE -ne 0) { throw 'Retain MetricFlow conversion failure' }
java -jar $dbJar export "$mfWork/baseline.ossie.yml" -o "$mfWork/baseline.metric-view.yml" --source sessions 1> "$mfWork/baseline-to-databricks.stdout.log" 2> "$mfWork/baseline-to-databricks.stderr.log"
$LASTEXITCODE | Set-Content "$mfWork/baseline-to-databricks.exit-code.txt"
if ($LASTEXITCODE -ne 0) { throw 'Retain Databricks export failure' }
Get-Content -LiteralPath "$mfWork/baseline-to-ossie.stderr.log"
Get-Content -LiteralPath "$mfWork/baseline-to-databricks.stderr.log"
Get-Content -LiteralPath "$mfWork/baseline.metric-view.yml"
```

Check the four `equal` values in `$mfWork/baseline/execution.json`: overall and channel queries for both metrics must match the independent oracle. [Captured baseline](../results/task05-minimal/metricflow/baseline/source/execution.json) contains the actual SQL and values.

`--source sessions` deliberately keeps non-buying sessions in the conversion denominator. The [actual generated Task 04 YAML](../results/task04-showcase/databricks/metricflow-to-databricks/generated.metric-view.yml) has a one-to-many orders join and 12 [conversion notices](../results/task04-showcase/databricks/metricflow-to-databricks/from-ossie.stderr.log). MetricFlow-qualified names `session__is_eligible` and `order__order_status` already became unresolved SQL tokens in Ossie and remain in Databricks. YAML parsing and exit 0 establish file conversion only. An analyzer rejection is expected from these references, but has **not been observed in Databricks**.

### Attempt the actual generated target in an authorized runtime

Prospective procedure: none of the SQL below has been run in Databricks here. Use a dedicated authorized Unity Catalog schema, source `SELECT`, `USE CATALOG`, `USE SCHEMA`, and schema `CREATE TABLE` privileges. Creation of views uses `CREATE TABLE` privilege according to [Databricks CREATE VIEW](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-view). A SQL warehouse or DBR **18.1+** must support this generated model's native `one_to_many` join. Record warehouse channel/version separately from cluster DBR; [feature availability](https://docs.databricks.com/aws/en/uc-semantics/metric-views/feature-availability) was checked on 2026-10-18.

1. Retain the exact generated YAML and its hash before any intervention. If `benchmark.main` is not an authorized source namespace, record the binding as blocked; do not create it merely to avoid recording a blocker.
2. Load the two canonical CSVs with explicit types. Use the [typed CSV ingestion procedure](../docs/DATABRICKS_HANDOFF.md#databricks-prerequisites-and-execution), which creates the **dbt-equivalent** `sessions` and `orders` views. That route needs notebook compute and authorized volume read/upload privileges in addition to SQL access. It preserves the session window and order observation cutoff; substituting unfiltered raw tables changes the source contract. Do not load `additional_order.csv`.
3. In a **separate copy**, bind only the two generated relation names to those source views. Keep the original, copied YAML, hashes and exact namespace diff. Classify this lane `NAMESPACE_BOUND_GENERATED`; further expression changes form another lane. Do not resolve tokens or alter joins silently.
4. Save this exact CREATE wrapper plus the inserted YAML as executed SQL. First attempt `mf_baseline_generated` with unchanged YAML if the original bindings are authorized; otherwise mark that lane BLOCKED and attempt the bound copy as `mf_baseline_bound`.

```sql
CREATE VIEW YOUR_CATALOG.YOUR_SCHEMA.mf_baseline_generated
WITH METRICS LANGUAGE YAML AS $$
-- Insert the retained baseline.metric-view.yml verbatim here.
$$;

SELECT MEASURE(conversion_rate) AS conversion_rate,
       MEASURE(paid_revenue) AS paid_revenue_eur
FROM YOUR_CATALOG.YOUR_SCHEMA.mf_baseline_generated;

SELECT sales_channel, MEASURE(conversion_rate) AS conversion_rate,
       MEASURE(paid_revenue) AS paid_revenue_eur
FROM YOUR_CATALOG.YOUR_SCHEMA.mf_baseline_generated
GROUP BY sales_channel ORDER BY sales_channel;

SHOW CREATE TABLE YOUR_CATALOG.YOUR_SCHEMA.mf_baseline_generated;
```

Run SELECT only if creation succeeds. For the bound lane change the view name consistently to `mf_baseline_bound`. Retain creation/analyzer errors separately from query results. Missing runtime access is `NOT_EXECUTED`; failed creation blocks numerical comparison. The generated YAML also lacks `session_month`; adding it would be another explicit intervention.

### Separate manually authored control

Use [the two-metric native control](databricks-task05/metric_view.baseline.yaml). Its source is a SQL LEFT JOIN, with explicit cohort/cutoff rules and eligibility filter. Copy [setup.sql](databricks-task05/setup.sql), bind `demo.semantic_interop` in the copy to a **different empty schema** and execute it there; it embeds precisely the same 12 sessions/six orders with BIGINT/DATE/BOOLEAN/DECIMAL types and uses CREATE without replacement. This control setup is not the generated lane's dbt-view setup.

Bind the control's source namespace in a copy, create `db_control_baseline` using the same wrapper, and run the same overall/channel queries against that name. Record the control's opening/creation/errors/results independently. The authored YAML's comment field needs compatible metadata support (DBR 17.3+). It has no native one-to-many join. The control remains **NOT_EXECUTED** until actually tested; its numerical success would not certify the generated path.

| Independent oracle | Overall | Direct | Indirect |
|---|---:|---:|---:|
| Conversion Rate, both revenue definitions | 0.4 | 0.5 | 0.25 |
| Baseline revenue EUR | 390.00 | 300.00 | 90.00 |
| Include-refunded revenue EUR | 440.00 | 300.00 | 140.00 |

The overall ratio is four converted / ten eligible sessions, **40%**, not the unweighted mean 37.5% of two channel rates. Preserve non-buyers and session-cohort attribution. Revenue is monetary; empty sums remain NULL. [Measured baseline source](../results/task05-minimal/metricflow/baseline/source/execution.json), [changed source](../results/task05-minimal/metricflow/include-refunded/source/execution.json), and [independent control oracle](../results/task05-minimal/databricks/oracle-results.json) back this table.

## Second experiment: include refunded revenue, 390 → 440 EUR

Edit only `$mfWork/dbt/models/metrics.yml`, metric **`paid_revenue_base`**. Replace its entire two-line `filter` field, including the continuation line, with the single line below. The original YAML doubles its inner quotes; replacing the whole field avoids quote errors. The only business change is including refunded orders:

```yaml
  filter: "{{ Dimension('session__is_eligible') }} = true AND {{ Dimension('order__order_status') }} IN ('paid', 'refunded')"
```

Keep Conversion Rate and its dependencies unchanged. Rebuild, execute, and convert the new manifest:

```powershell
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$mfWork/dbt" --profiles-dir "$mfWork/dbt" 1> "$mfWork/build-refunded.stdout.log" 2> "$mfWork/build-refunded.stderr.log"
if ($LASTEXITCODE -ne 0) { throw 'Edited dbt build failed' }
& $py $helper execute --work $mfWork --label include-refunded --include-refunded
if ($LASTEXITCODE -ne 0) { throw 'Edited source execution failed' }
& $py -m ossie_dbt.cli msi-to-ossie -i "$mfWork/dbt/target/semantic_manifest.json" -o "$mfWork/include-refunded.ossie.yml" --model-name conversion_demo 1> "$mfWork/refunded-to-ossie.stdout.log" 2> "$mfWork/refunded-to-ossie.stderr.log"
$LASTEXITCODE | Set-Content "$mfWork/refunded-to-ossie.exit-code.txt"
if ($LASTEXITCODE -ne 0) { throw 'Retain edited import failure' }
java -jar $dbJar export "$mfWork/include-refunded.ossie.yml" -o "$mfWork/include-refunded.metric-view.yml" --source sessions 1> "$mfWork/refunded-to-databricks.stdout.log" 2> "$mfWork/refunded-to-databricks.stderr.log"
$LASTEXITCODE | Set-Content "$mfWork/refunded-to-databricks.exit-code.txt"
if ($LASTEXITCODE -ne 0) { throw 'Retain edited export failure' }
```

Inspect [captured changed output](../results/task05-minimal/metricflow/include-refunded/output.metric-view.yml): refund membership text survives together with unresolved identifiers. This is static evidence, not a 440 EUR Databricks result. Attempt it as `mf_refunded_generated` / `mf_refunded_bound`, keeping the same lane rules and queries.

For the separate native control, copy the baseline YAML and change **only `paid_revenue`** from `SUM(amount_eur) FILTER (WHERE order_status = 'paid')` to `SUM(amount_eur) FILTER (WHERE order_status IN ('paid', 'refunded'))`. Create `db_control_refunded` and run the same queries. Compare source, unchanged/bound converted targets, and authored controls in separate records. Full manual edits, comparison commands and reset instructions are in [Exercise C](TUTORIAL.md#exercise-c-databricks-include-refunded-orders).

Reset the working MetricFlow source, rebuild, then execute with a fresh `--label restored`. It should return 390 EUR and 40%. Canonical fixtures and accepted results need no restoration.

```powershell
Copy-Item -LiteralPath labs/semantic_interop/fixtures/conversion_demo/dbt/models/metrics.yml -Destination "$mfWork/dbt/models/metrics.yml"
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$mfWork/dbt" --profiles-dir "$mfWork/dbt"
if ($LASTEXITCODE -ne 0) { throw 'Restored dbt build failed' }
& $py $helper execute --work $mfWork --label restored
```

## Optional reverse direction: native Databricks → Ossie → Databricks

This retains a Databricks definition through its own adapter. Use a fresh directory, then the same JAR:

```powershell
$dbNative = '.tmp/charlotte-native-roundtrip-001'
New-Item -ItemType Directory -Path $dbNative -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath labs/semantic_interop/showcase/databricks-task05/metric_view.baseline.yaml -Destination "$dbNative/source.metric-view.yml"
java -jar $dbJar import "$dbNative/source.metric-view.yml" -o "$dbNative/imported.ossie.yml" --name task05_databricks 1> "$dbNative/import.stdout.log" 2> "$dbNative/import.stderr.log"
$LASTEXITCODE | Set-Content "$dbNative/import.exit-code.txt"
if ($LASTEXITCODE -ne 0) { throw 'Retain native import failure' }
java -jar $dbJar export "$dbNative/imported.ossie.yml" -o "$dbNative/roundtrip.metric-view.yml" 1> "$dbNative/export.stdout.log" 2> "$dbNative/export.stderr.log"
$LASTEXITCODE | Set-Content "$dbNative/export.exit-code.txt"
if ($LASTEXITCODE -ne 0) { throw 'Retain native export failure' }
```

The [captured baseline comparison](../results/task05-minimal/databricks/baseline/comparison.json) and [changed comparison](../results/task05-minimal/databricks/include-refunded/comparison.json) preserve complete YAML values after trimming only trailing whitespace of the entire SQL source string. Measures use dialect `DATABRICKS`; the global eligibility filter survives in a `DATABRICKS` extension. This demonstrates bounded vendor-definition retention. Native target execution is still unverified. If authorized, execute the returned YAML as its own named view and compare it to the authored control.

## Report one record per experiment and lane

Retain exact source and generated artifacts/hashes; converter commands, stdout, stderr, warnings and exit codes; setup/CREATE/SELECT/SHOW CREATE SQL; raw typed result exports (including NULLs); runtime/channel; all manual diffs; repository/base/converter commits and tool versions. Record unexpected values separately from a creation failure or absent runtime. Use statuses **PASS, MISMATCH, FAIL, BLOCKED, NOT_EXECUTED, UNKNOWN** as defined in [RESULTS.md](RESULTS.md).

```json
{
  "repository_base_main_sha": "RECORD_AFTER_MERGE",
  "contribution_sha": "",
  "experiment": "baseline | include-refunded | native-roundtrip",
  "lane": "UNCHANGED_GENERATED | NAMESPACE_BOUND_GENERATED | AUTHORED_CONTROL",
  "python_converter_commit": "6ebc5e5612467dac405985f44ea078b39c891bee",
  "java_converter_commit": "53418ba8a4b8a20951334fdb9d9638aa34b514e3",
  "jar_sha256": "", "java_version": "", "maven_version": "",
  "warehouse_or_runtime_version_and_channel": "", "namespace": "",
  "conversion": "UNKNOWN", "static_semantics": "UNKNOWN",
  "native_target_queries": "NOT_EXECUTED", "oracle_agreement": "BLOCKED",
  "interventions_and_diff_paths": [], "warning_and_error_paths": [],
  "setup_sql": "", "create_sql": "", "query_sql": "", "show_create_sql": "",
  "actual_results_path": "", "oracle_results_path": "",
  "expected_overall": {"conversion_rate": 0.4, "paid_revenue_eur": "390.00"},
  "actual_overall": null,
  "evidence_files_and_sha256": {}
}
```

For the refund lane use expected revenue `440.00`; include Direct/Indirect results too. The existing [runtime JSON template](databricks-task05/runtime-results-template.json) provides more granular per-query fields. A result must identify whether any returned number comes from the source, independent oracle, authored control, unchanged generated model, or edited generated model.

Troubleshooting: missing Java/Maven means provisioning is incomplete; preserve build logs. Unresolved aliases, one-to-many restrictions and dropped fields require the original generated bytes plus analyzer output, before any separately labeled repair. Do not switch the source to orders to obtain a pass: that can lose non-buying sessions. EUR 190/200 by month indicates transaction-month grouping instead of the required EUR 270/120 session cohorts. The [supporting guide](../docs/DATABRICKS_HANDOFF.md#capture-template-and-troubleshooting) gives detailed diagnostics.

Scope: the same canonical fixtures, pinned official converters, numerical execution and an evidence-backed report. No new fixtures, converter development, frontend/backend, silent generated-model fixes or baseline dependency changes. The remaining external prerequisite is authorized Databricks runtime access. This guide and tracked inputs on `main` are sufficient to begin; no unpublished local environment or credential is required for the offline work.

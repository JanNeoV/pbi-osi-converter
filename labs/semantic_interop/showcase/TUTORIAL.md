# Hands-on interoperability: three independent exercises

Choose [A: MetricFlow](#exercise-a-metricflow-include-refunded-orders),
[B: Power BI](#exercise-b-power-bi-change-the-default-kpi), or
[C: Databricks](#exercise-c-databricks-include-refunded-orders). Each starts from a supplied model;
you do not need to complete the other exercises. For the business story, start with the
[three-experiment quickstart](QUICKSTART.md).

The shared fixture has [12 sessions](../fixtures/conversion_demo/data/sessions.csv) and
[six orders](../fixtures/conversion_demo/data/orders.csv). Two sessions are ineligible. Four of
the ten eligible sessions have paid orders, so Conversion Rate is **4 / 10 = 40%**. Direct is
3 / 6 = **50%** and Indirect is 1 / 4 = **25%**. Overall is the ratio of the totals, not the
unweighted average of the channel percentages (37.5%). Paid Revenue is **300 + 90 = 390 EUR**.
Including the existing 50 EUR refunded order changes revenue to **440 EUR**, without changing
the paid-session conversion definition.

All models use January 1 inclusive through March 1 exclusive, 2026 session cohorts, with
orders observed before March 1. Keep sessions without orders and attribute revenue to the
originating session's cohort. Work on copies under `.tmp/`; retain the original generated
files, warnings and errors. An adapter's exit code 0 proves file conversion completed.
Numerical equivalence requires executing that generated model in its real target engine.

## Local setup for any exercise

Run the commands from the **repository root** in Windows PowerShell. The checkout location
can differ from the example below. Prerequisites: Git, Python **3.12**, and **uv 0.12.17** on
`PATH`. Installation needs internet access for public pinned dependencies; the Python
queries and converter commands then run locally without accounts or credentials.

```powershell
Set-Location C:\github\pbi-osi-converter
python --version
python -m pip install uv==0.12.17
uv --version
python labs/semantic_interop/scripts/setup.py
if ($LASTEXITCODE -ne 0) { throw 'Pinned setup failed' }
$py = '.tmp/semantic-lab-venv/Scripts/python.exe'
```

The setup script uses [`uv.lock`](../uv.lock) with `--frozen` and leaves the parent
environment separate. Do not upgrade an already installed lab environment. If `python`
selects another version on Windows, use `py -3.12` for the bootstrap commands.

The Python Ossie adapters are **0.2.0.dev0**, upstream commit
`6ebc5e5612467dac405985f44ea078b39c891bee`. The Databricks adapter is a separate
**0.1.0-SNAPSHOT** Java build pinned to `53418ba8a4b8a20951334fdb9d9638aa34b514e3`.
[Recorded tool provenance](../results/task05-minimal/metricflow/provenance.json) describes
the measured environment; ignored `.tmp/` binaries are not distributed with this repository.

Use a new numbered work directory when repeating an exercise. Helpers refuse existing
directories or result labels. Every command below uses repository files; Desktop and
Databricks runtime steps are explicitly marked as work still to perform.

## Exercise A: MetricFlow include refunded orders

**Objective:** change an executable source metric from 390 to 440 EUR, then inspect what
survives MetricFlow → Ossie → MetricFlow. **Prerequisites:** local Python setup above.
No Power BI, Java or Databricks installation is needed.

### A1. Copy the real source and run the baseline

Source definition: [`fixtures/conversion_demo/dbt/models/metrics.yml`](../fixtures/conversion_demo/dbt/models/metrics.yml).
The helper copies the entire canonical dbt project and loads its rows into local DuckDB.
It compiles queries through real MetricFlow and compares their results with independent
raw-table SQL; it contains no converter.

```powershell
$py = '.tmp/semantic-lab-venv/Scripts/python.exe'
$helper = 'labs/semantic_interop/showcase/metricflow_task05.py'
$work = '.tmp/task06-tutorial-mf-001'
& $py $helper init --work $work
if ($LASTEXITCODE -ne 0) { throw 'Use a fresh work directory' }
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$work/dbt" --profiles-dir "$work/dbt" 1> "$work/dbt-build-before.stdout.log" 2> "$work/dbt-build-before.stderr.log"
$LASTEXITCODE | Set-Content "$work/dbt-build-before.exit-code.txt"
& $py $helper execute --work $work --label before
```

Open `$work/before/execution.json`. All four comparisons should have `equal: true`:
revenue and conversion, both overall and by channel. Expected values are 390 EUR / 0.4,
Direct 300 EUR / 0.5, Indirect 90 EUR / 0.25. Generated SQL is under `before/sql/` and
independent reference SQL is in `before/oracle-*.sql`. Row order can differ, and MetricFlow
calls the grouping column `session__sales_channel`; the comparison handles both details.
The [captured baseline](../results/task05-minimal/metricflow/baseline/source/execution.json)
contains these measured results.

### A2. Edit one filter, rebuild and rerun

Open **`.tmp/task06-tutorial-mf-001/dbt/models/metrics.yml`**. Under
`name: paid_revenue_base`, replace the entire existing `filter:` field, including its
continued line, with this single line at the same indentation:

```yaml
  filter: "{{ Dimension('session__is_eligible') }} = true AND {{ Dimension('order__order_status') }} IN ('paid', 'refunded')"
```

Only the order-status predicate changes from `= 'paid'` to `IN ('paid', 'refunded')`.
Leave `converted_sessions_base`, the public `paid_revenue` dependency expression and every
other filter unchanged. The filter is a sibling of `type_params`, not nested inside it.

```powershell
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$work/dbt" --profiles-dir "$work/dbt" 1> "$work/dbt-build-after.stdout.log" 2> "$work/dbt-build-after.stderr.log"
$LASTEXITCODE | Set-Content "$work/dbt-build-after.exit-code.txt"
& $py $helper execute --work $work --label after --include-refunded
```

Expect all four `equal` fields to be true in `after/execution.json`: overall **440 EUR / 40%**,
Direct **300 EUR / 50%**, Indirect **140 EUR / 25%**. `--include-refunded` selects the matching
independent oracle scenario; it does not edit the model. The [captured edited source](../results/task05-minimal/metricflow/include-refunded/source-models/metrics.yml)
and [actual results](../results/task05-minimal/metricflow/include-refunded/source/execution.json)
show this exact experiment.

### A3. Convert with the official adapters and attempt the return

```powershell
& $py -m ossie_dbt.cli msi-to-ossie -i "$work/dbt/target/semantic_manifest.json" -o "$work/after.ossie.yml" --model-name conversion_demo 1> "$work/to-ossie.stdout.log" 2> "$work/to-ossie.stderr.log"
$LASTEXITCODE | Set-Content "$work/to-ossie.exit-code.txt"
& $py -m ossie_dbt.cli ossie-to-msi -i "$work/after.ossie.yml" -o "$work/after.returned.json" 1> "$work/from-ossie.stdout.log" 2> "$work/from-ossie.stderr.log"
$LASTEXITCODE | Set-Content "$work/from-ossie.exit-code.txt"
Get-Content "$work/to-ossie.stderr.log"
Get-Content "$work/from-ossie.stderr.log"
& $py $helper execute --work $work --label after-target --manifest "$work/after.returned.json" --include-refunded
Select-String -Path "$work/after.ossie.yml" -Pattern 'refunded','session__is_eligible','order__order_status' -Context 1,1
```

Open `after.ossie.yml`: the refund predicate travels, but its logical MetricFlow filter
names are unresolved SQL identifiers. Open `after.returned.json`: the return lacks time-spine
configuration, entities and aggregation-time defaults, and compound metric representation
changes. `after-target/execution.json` has `equal: null` and the error **“At least one time
spine must be configured…”**. No target query executed; 440 is a source result only.
Both conversion commands historically exited 0. See the [unchanged generated return](../results/task05-minimal/metricflow/include-refunded/output.manifest.json)
and [captured target attempt](../results/task05-minimal/metricflow/include-refunded/target/execution.json).
Keep that failure as evidence. Adding configuration would be a separate experiment.

### A4. Reset and troubleshoot

Restore only your copied source, rebuild and use a new result label:

```powershell
Copy-Item -LiteralPath labs/semantic_interop/fixtures/conversion_demo/dbt/models/metrics.yml -Destination "$work/dbt/models/metrics.yml"
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$work/dbt" --profiles-dir "$work/dbt"
& $py $helper execute --work $work --label restored
```

Expect **390 EUR / 40%** again. Existing `after.*` files remain the edited experiment;
use new output names if converting the restored source. For YAML errors, remove the old
continued filter line and check indentation. If values stay at 390, check you edited
`paid_revenue_base` in the copied project and that dbt build exited 0. If a folder/label
already exists, choose a new one. The missing-spine target error is the observed limitation.

The [diagnosis](TASK05_DIAGNOSIS.md) covers the additional elementary aggregate and separate
spine reconstruction. Neither established a numerically verified unchanged conversion path.

## Exercise B: Power BI change the default KPI

**Objective:** create and execute the two headline measures, add a disconnected KPI selector,
change its default from Conversion Rate to Paid Revenue, and compare original/returned DAX.
**Prerequisites:** Windows Power BI Desktop supporting PBIP, DAX Query View and dynamic
measure format strings. The Python setup is needed only for offline conversion. If Desktop
saves your model as TMDL, a locally installed Tabular Editor external tool is needed to
export TMSL for this pinned CLI; record both application versions.

**Delivered candidate: NOT_VERIFIED.** [`baseline.model.bim`](powerbi-task05/baseline.model.bim)
is an authored TMSL candidate with embedded copies of the canonical rows. There is no verified
ready-to-open PBIP/PBIX. Desktop opening, refresh, DAX execution, slicer behavior and visual
formatting remain unperformed. The official converters have processed both candidate versions;
their metadata retention is measured separately from runtime correctness.

### B1. Open the candidate or create the model manually

To try the candidate, create a blank Desktop report, import `Sessions.pq` as **Sessions**
using the Blank query procedure below, and **Close & Apply** to create a local model.
Choose **File > Save As > Power BI Project** at
`.tmp/task06-pbi-desktop/ConversionDemo.pbip`, then close Desktop. Keep Desktop's generated
PBIP, report folder and `ConversionDemo.SemanticModel/definition.pbism`. In that semantic-model
folder, use `baseline.model.bim` as `model.bim`; if a `definition/` folder exists, move it into
a backup outside the semantic-model folder first. Use one model format at a time.
Microsoft's [semantic-model folder documentation](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-dataset)
describes TMSL `model.bim` and TMDL `definition/`; check that the generated `definition.pbism`
version supports TMSL. Reopen the PBIP, refresh and record any errors. This loading procedure
still needs a Desktop owner to execute it.

For a manual model, or if the candidate cannot load:

1. Create a blank report. Under **File > Options and settings > Options > Current File >
   Data Load**, disable Auto date/time for this example.
2. Choose **Get data > Blank query**, open Power Query's **Advanced Editor**, and paste
   [`Sessions.pq`](powerbi-task05/Sessions.pq). Name the query **Sessions**. Repeat with
   [`Orders.pq`](powerbi-task05/Orders.pq) as **Orders**,
   [`Sales Channel.pq`](<powerbi-task05/Sales Channel.pq>) as **Sales Channel**, and
   [`KPI Selector.pq`](<powerbi-task05/KPI Selector.pq>) as **KPI Selector**. **Close & Apply**.
   The literal tables contain the unchanged 12 sessions and six orders; no CSV paths or
   credentials are required. Check row counts and that dates, booleans and amounts have the
   explicit types in those files.
3. In **Manage relationships**, keep exactly these active, single-direction relationships:
   `Orders[session_id]` many → `Sessions[session_id]` one, and
   `Sessions[sales_channel]` many → `'Sales Channel'[sales_channel]` one. The one side filters
   the many side. Leave **KPI Selector disconnected**; remove any auto-created selector
   relationship. [Microsoft's relationship instructions](https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-create-and-manage-relationships)
   explain the dialog.
4. Select **Sessions**, choose **New measure**, and create the following four measures,
   one at a time. These are the expressions in the supplied named `.dax` files.

```dax
Eligible Sessions =
COALESCE(
    CALCULATE(
        DISTINCTCOUNT(Sessions[session_id]),
        KEEPFILTERS(Sessions[is_eligible] = TRUE()),
        KEEPFILTERS(Sessions[session_date] >= DATE(2026, 1, 1)),
        KEEPFILTERS(Sessions[session_date] < DATE(2026, 3, 1))
    ),
    0
)
```

```dax
Converted Sessions =
COALESCE(
    CALCULATE(
        DISTINCTCOUNT(Orders[session_id]),
        KEEPFILTERS(Sessions[is_eligible] = TRUE()),
        KEEPFILTERS(Sessions[session_date] >= DATE(2026, 1, 1)),
        KEEPFILTERS(Sessions[session_date] < DATE(2026, 3, 1)),
        KEEPFILTERS(Orders[order_status] = "paid"),
        KEEPFILTERS(Orders[order_date] < DATE(2026, 3, 1))
    ),
    0
)
```

```dax
Conversion Rate =
DIVIDE([Converted Sessions], [Eligible Sessions])
```

```dax
Paid Revenue =
CALCULATE(
    SUM(Orders[amount_eur]),
    KEEPFILTERS(Sessions[is_eligible] = TRUE()),
    KEEPFILTERS(Sessions[session_date] >= DATE(2026, 1, 1)),
    KEEPFILTERS(Sessions[session_date] < DATE(2026, 3, 1)),
    KEEPFILTERS(Orders[order_status] = "paid"),
    KEEPFILTERS(Orders[order_date] < DATE(2026, 3, 1))
)
```

Set **Conversion Rate** format to `0.0%` and **Paid Revenue** to `#,0.00 "EUR"`.
In **DAX Query View**, execute:

```dax
EVALUATE
ROW("Conversion Rate", [Conversion Rate], "Paid Revenue", [Paid Revenue])
```

Expected raw results are **0.4 and 390**. These are independently measured
[DuckDB expectations](../results/task05-minimal/powerbi/expected-values.json), not already
observed DAX results. Save actual query output and Desktop version locally.

### B2. Add and test the dynamic selector

The imported selector has exactly two rows: `Conversion Rate` and `Paid Revenue`.
Create this fifth measure under Sessions:

```dax
Selected KPI =
SWITCH(
    SELECTEDVALUE('KPI Selector'[KPI], "Conversion Rate"),
    "Conversion Rate", [Conversion Rate],
    "Paid Revenue", [Paid Revenue],
    BLANK()
)
```

Select the measure, choose **Measure tools > Format > Dynamic**, switch the formula bar
to its **Format** expression, and paste [`Selected KPI.format.dax`](<powerbi-task05/Selected KPI.format.dax>):

```dax
SWITCH(
    SELECTEDVALUE('KPI Selector'[KPI], "Conversion Rate"),
    "Conversion Rate", "0.0%",
    "Paid Revenue", "#,0.00 ""EUR""",
    "General Number"
)
```

[Microsoft's dynamic-format guide](https://learn.microsoft.com/en-us/power-bi/create-reports/desktop-dynamic-format-strings)
describes the measure/format selector. Add a Card showing `[Selected KPI]`, a slicer using
`'KPI Selector'[KPI]`, and a channel slicer using `'Sales Channel'[sales_channel]`.
Allow clearing or multiple selections. Add a table with channel and both headline measures.
Run [`validate.dax`](powerbi-task05/validate.dax) in DAX Query View and capture results.
DAX queries do not inherit report slicers; the supplied query uses `TREATAS` for channel tests.

In **both** the measure and its dynamic format expression, change:

```dax
SELECTEDVALUE('KPI Selector'[KPI], "Conversion Rate"),
```

to:

```dax
SELECTEDVALUE('KPI Selector'[KPI], "Paid Revenue"),
```

Leave the final `SWITCH` fallback as `BLANK()`. “Default” here means the alternate result of
`SELECTEDVALUE` when zero or multiple distinct KPI values are in filter context. Rerun the
queries, clear the slicer, select each choice, and select both:

| Selection/context | Baseline expected display | Changed expected display |
|---|---:|---:|
| No selection or both choices, all channels | 40% | 390 EUR |
| No selection or both choices, Direct | 50% | 300 EUR |
| No selection or both choices, Indirect | 25% | 90 EUR |
| Explicit Conversion Rate, all channels | 40% | 40% |
| Explicit Paid Revenue, all channels | 390 EUR | 390 EUR |

These are runtime acceptance criteria still to perform. Query numbers alone do not verify
card units or slicer interactions. Capture both in the supplied
[`runtime-results-template.csv`](powerbi-task05/runtime-results-template.csv).

### B3. Export model metadata and run Power BI → Ossie → Power BI

**Desktop export, to perform manually:** use **File > Save As > Power BI Project** to retain
the model and report as documented [PBIP files](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview).
Save baseline and changed projects separately. A TMSL project has `model.bim`; copy that
exact file as converter input. A TMDL project has `definition/`; retain that folder as
evidence, then export the **open local model** to a TMSL `.bim` using **External Tools >
Tabular Editor > File > Save As**. Select model metadata, not a supporting script, and save
as `source.model.bim` in a fresh work directory. The
[external-tool integration](https://docs.tabulareditor.com/getting-started/Power-BI-Desktop-Integration.html)
and [Save As behavior](https://docs.tabulareditor.com/en/features/views/user-interface.html)
are documented by Tabular Editor. Record the tool version and keep this export separate
from the supplied candidate. The pinned Ossie CLI takes TMSL JSON, not a `.pbip`, `.pbix` or
TMDL folder. No Desktop export or TMDL serialization has been validated in this lab.

**Executable offline candidate exercise:** this works without Desktop. Copy the candidate,
then edit the two defaults in that copied JSON as above. They live in the `Selected KPI`
measure's `expression` and `formatStringDefinition.expression`; JSON escapes quotes as `\"`.
[`changed-default.model.bim`](powerbi-task05/changed-default.model.bim) is the finished
comparison specimen. To test your own Desktop export instead, replace only the `Copy-Item`
source below with your exported `.bim` and retain its unmodified bytes.

```powershell
$py = '.tmp/semantic-lab-venv/Scripts/python.exe'
$work = '.tmp/task06-tutorial-pbi-001'
New-Item -ItemType Directory -Path $work -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath labs/semantic_interop/showcase/powerbi-task05/baseline.model.bim -Destination "$work/source.model.bim"
# Edit the two defaults in this copied source.model.bim before continuing.
& $py -m ossie_microsoft.cli import -i "$work/source.model.bim" -o "$work/output.ossie.yml" 1> "$work/to-ossie.stdout.log" 2> "$work/to-ossie.stderr.log"
$LASTEXITCODE | Set-Content "$work/to-ossie.exit-code.txt"
& $py -m ossie_microsoft.cli export -i "$work/output.ossie.yml" -o "$work/roundtrip.model.bim" 1> "$work/from-ossie.stdout.log" 2> "$work/from-ossie.stderr.log"
$LASTEXITCODE | Set-Content "$work/from-ossie.exit-code.txt"
Get-Content "$work/to-ossie.stderr.log"
Get-Content "$work/from-ossie.stderr.log"
$source = Get-Content "$work/source.model.bim" -Raw | ConvertFrom-Json
$target = Get-Content "$work/roundtrip.model.bim" -Raw | ConvertFrom-Json
($source.model.tables | Where-Object name -eq Sessions).measures | Where-Object name -eq 'Selected KPI' | ConvertTo-Json -Depth 20
($target.model.tables | Where-Object name -eq Sessions).measures | Where-Object name -eq 'Selected KPI' | ConvertTo-Json -Depth 20
$target.model.relationships | ConvertTo-Json -Depth 20
```

**macOS/Linux (offline candidate, no Desktop):** After the pinned setup in
[QUICKSTART](QUICKSTART.md#setup-and-run), run the official pinned converters on
**both supplied candidate files**. Each input is kept intact; the difference is
the `SELECTEDVALUE` alternate result in the measure and dynamic format.
The shell commands use fresh work directories and fail rather than overwrite
existing evidence:

```sh
PY=.tmp/semantic-lab-venv/bin/python
for variant in baseline changed-default; do
  WORK=".tmp/pbi-selector-${variant}"
  mkdir "$WORK" || exit 1
  cp "labs/semantic_interop/showcase/powerbi-task05/${variant}.model.bim" "$WORK/source.model.bim"
  "$PY" -m ossie_microsoft.cli import -i "$WORK/source.model.bim" -o "$WORK/output.ossie.yml" >"$WORK/import.stdout.log" 2>"$WORK/import.stderr.log" || exit 1
  "$PY" -m ossie_microsoft.cli export -i "$WORK/output.ossie.yml" -o "$WORK/roundtrip.model.bim" >"$WORK/export.stdout.log" 2>"$WORK/export.stderr.log" || exit 1
  "$PY" - "$WORK/source.model.bim" "$WORK/roundtrip.model.bim" <<'PYCODE' || exit 1
import json, sys
source, returned = [json.load(open(p, encoding="utf-8")) for p in sys.argv[1:]]
def selector(doc):
    return next(m for t in doc["model"]["tables"] if t["name"] == "Sessions"
                for m in t.get("measures", []) if m["name"] == "Selected KPI")
a, b = selector(source), selector(returned)
def norm(v):
    return chr(10).join(v) if isinstance(v, list) else v
assert norm(a["expression"]) == norm(b["expression"]), "DAX selector expression changed"
assert norm(a["formatStringDefinition"]["expression"]) == norm(b["formatStringDefinition"]["expression"]), "Dynamic format changed"
print("Selector DAX and dynamic-format expressions retained (static only)")
PYCODE
done
```

For the full normalized-document, relationship and dependency comparison,
consult the [existing captured baseline/edited checks](../results/task05-minimal/powerbi/report.json).
The short snippet above verifies **only** the two selector expressions; it does
not assert Desktop loadability or numeric equivalence. The supplied
`changed-default.model.bim` is an authored example, not output from editing
the model in a live Desktop session.

Repeat in another directory without changing the defaults to compare the baseline.
[Actual candidate evidence](../results/task05-minimal/powerbi/report.json) shows both versions
returned the complete normalized model unchanged: DAX, dependencies, literal M rows,
disconnected selector, relationships and dynamic format expression. All eleven bounded
checks passed. Normalization ignores named-object order and the legal TMSL serialization
of expression text as a string versus an array of lines. Import warned that shared Power
Query expressions have no Ossie counterpart
and were retained in the Power BI stash; export had no warning. These native extensions
can restore Power BI metadata. They do not translate DAX `SWITCH` into other platforms'
execution semantics.

**Runtime check still to perform:** duplicate your saved Desktop project, close Desktop,
replace its semantic model with the actual `roundtrip.model.bim` using B1's TMSL procedure,
reopen, refresh, and repeat `validate.dax` and the visual tests. Record source and returned
results separately. A manually recreated model does not validate the generated model.

### B4. Reset and troubleshoot

Restore the two alternate-result strings to `"Conversion Rate"` in both expressions, or
reopen a saved baseline project. For offline work, start from `baseline.model.bim` in a new
directory. If channel results repeat the overall value, check relationship directions and
use the channel dimension. If the card displays a huge percentage after the change, check
the dynamic **Format** expression as well as the measure. If the candidate fails to load,
retain the error and use B1's manual setup; keep its validation record separate. Do not
mark the delivered candidate verified until it has actually opened, refreshed and executed.

For cross-platform limits, see [the diagnosis](TASK05_DIAGNOSIS.md): Power BI → MetricFlow
retains DAX text under SUM fallback semantics; Power BI → Databricks fails at the disconnected
selector. MetricFlow → Power BI can emit annotated `BLANK()` substitutions. These findings
do not establish DAX runtime correctness.

## Exercise C: Databricks include refunded orders

**Objective:** change the two-headline Metric View and compare its definition before and
after official Databricks → Ossie → Databricks conversion. **Prerequisites:** Git, Java 21
and Maven for the pinned converter build; local Python setup for YAML comparison and the
DuckDB oracle. Native conversion needs no Databricks login. Native Metric View execution
requires an authorized Databricks runtime and Unity Catalog namespace.

### C1. Build the pinned adapter and copy the source

Follow the [handoff's pinned Java build](DATABRICKS_HANDOFF.md#converter-versions-and-local-prerequisites).
It installs no Java binaries into the repository and builds official upstream source in a
new ignored directory. Do not assume historical local binaries exist. After that build,
use its `$dbJar`, or set the same path below. A fresh build has its own JAR hash; record it.

```powershell
$py = '.tmp/semantic-lab-venv/Scripts/python.exe'
$dbJar = '.tmp/charlotte-ossie-001/converters/databricks/java/target/ossie-databricks-converter-0.1.0-SNAPSHOT.jar'
java -version
Get-FileHash -Algorithm SHA256 -LiteralPath $dbJar
$dbWork = '.tmp/task06-tutorial-db-001'
New-Item -ItemType Directory -Path "$dbWork/baseline","$dbWork/include-refunded" -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath labs/semantic_interop/showcase/databricks-task05/metric_view.baseline.yaml -Destination "$dbWork/baseline/source.metric-view.yml"
Copy-Item -LiteralPath labs/semantic_interop/showcase/databricks-task05/metric_view.baseline.yaml -Destination "$dbWork/include-refunded/source.metric-view.yml"
```

Open [`databricks-task05/metric_view.baseline.yaml`](databricks-task05/metric_view.baseline.yaml).
This is an **authored reduced native control**, not output generated from MetricFlow. It
keeps the canonical source rows, channel dimension and two headlines. Its source LEFT JOIN
retains sessions without orders. `filter: is_eligible = true` applies to both measures.
Conversion Rate counts distinct paid sessions / distinct eligible sessions; Paid Revenue
sums paid amounts. This reduction's native runtime remains **NOT_EXECUTED**.

### C2. Make the revenue edit and convert both definitions

In **`.tmp/task06-tutorial-db-001/include-refunded/source.metric-view.yml`**, change only
the `paid_revenue` expression:

```yaml
# Before
expr: "SUM(amount_eur) FILTER (WHERE order_status = 'paid')"
# After
expr: "SUM(amount_eur) FILTER (WHERE order_status IN ('paid', 'refunded'))"
```

Keep the metric name for comparison; its edited meaning includes refunds. Leave Conversion
Rate unchanged. Run the actual official converters; `import` means native Metric View →
Ossie and `export` means Ossie → native Metric View:

```powershell
foreach ($lane in @('baseline','include-refunded')) {
  $path = "$dbWork/$lane"
  java -jar $dbJar import "$path/source.metric-view.yml" -o "$path/imported.ossie.yml" --name task05_databricks 1> "$path/to-ossie.stdout.log" 2> "$path/to-ossie.stderr.log"
  $LASTEXITCODE | Set-Content "$path/to-ossie.exit-code.txt"
  java -jar $dbJar export "$path/imported.ossie.yml" -o "$path/roundtrip.metric-view.yml" 1> "$path/from-ossie.stdout.log" 2> "$path/from-ossie.stderr.log"
  $LASTEXITCODE | Set-Content "$path/from-ossie.exit-code.txt"
  Get-Content "$path/to-ossie.stderr.log"
  Get-Content "$path/from-ossie.stderr.log"
}
```

### C3. Inspect definitions and run the independent numerical check

Open each source, `imported.ossie.yml`, returned YAML and stderr log. Find the refund
predicate under `metrics[name=paid_revenue].expression.dialects`, tagged **DATABRICKS**.
The global eligibility filter is stored in the DATABRICKS vendor extension and restored
by its native exporter. Compare complete parsed YAML values, ignoring only trailing
whitespace on the whole source SQL string:

```powershell
foreach ($lane in @('baseline','include-refunded')) {
  & $py -c "import sys,yaml; a=yaml.safe_load(open(sys.argv[1])); b=yaml.safe_load(open(sys.argv[2])); a['source']=a['source'].rstrip(); b['source']=b['source'].rstrip(); print(a==b)" "$dbWork/$lane/source.metric-view.yml" "$dbWork/$lane/roundtrip.metric-view.yml"
}
& $py -c "import pathlib,duckdb; c=duckdb.connect(); statements=c.extract_statements(pathlib.Path('labs/semantic_interop/showcase/databricks-task05/oracle.sql').read_text()); [(c.execute(s), print(c.fetchall()) if s.type.name=='SELECT' else None) for s in statements]"
```

Measured comparison output is `True`, `True`; all four Java commands exited 0 with empty
stderr. See [baseline comparison](../results/task05-minimal/databricks/baseline/comparison.json)
and [edited comparison](../results/task05-minimal/databricks/include-refunded/comparison.json).
The oracle reads canonical CSVs and executes raw-table DuckDB SQL, not a Databricks Metric
View. Its [measured results](../results/task05-minimal/databricks/oracle-results.json) are:

| Cohort | Conversion Rate, before and after | Revenue before | Revenue after |
|---|---:|---:|---:|
| Overall | 40% | 390 EUR | 440 EUR |
| Direct | 50% | 300 EUR | 300 EUR |
| Indirect | 25% | 90 EUR | 140 EUR |

Definition retention and oracle numbers are separate evidence. No Databricks analyzer or
native metric query has executed here. For the optional cloud step, use the
[handoff](DATABRICKS_HANDOFF.md) to load the same typed fixtures, bind a copy to your authorized
catalog/schema, create a Metric View from the **actual returned YAML**, and run
[`validate.sql`](databricks-task05/validate.sql). Capture errors before making separately
labeled changes. The handoff also supplies the distinct MetricFlow → Ossie → Databricks
experiment; this native control does not establish cross-vendor equivalence.

### C4. Reset and troubleshoot

```powershell
Copy-Item -LiteralPath labs/semantic_interop/showcase/databricks-task05/metric_view.baseline.yaml -Destination "$dbWork/include-refunded/source.metric-view.yml"
```

Completed outputs remain the prior experiment; use a new directory to rerun. If Java or
the JAR is missing, complete the pinned build in C1; Python setup does not install them.
For YAML inequality, inspect the entire parsed objects and both stderr logs. A future
Databricks permission/version/analyzer error is a runtime blocker, not an observed wrong
number. Record actual SQL, versions, generated/bound YAML hashes and results in
[`runtime-results-template.json`](databricks-task05/runtime-results-template.json).

## What these exercises establish

| Exercise | Measured source/control result | Official native return | Target runtime |
|---|---|---|---|
| A: MetricFlow revenue edit | 390 → 440 EUR; conversion stays 40% | Files emitted; required semantics/configuration lost | BLOCKED at missing time spine |
| B: Power BI default edit | DuckDB reference 40% / 390 EUR; DAX values unmeasured | Inspected candidate metadata retained | NOT_EXECUTED; candidate NOT_VERIFIED |
| C: Databricks revenue edit | DuckDB oracle 390 → 440 EUR; conversion stays 40% | Inspected YAML values retained | NOT_EXECUTED |

There is **no numerically verified unchanged conversion happy path** in these results.
Use the [nine-path results matrix](RESULTS.md) and [diagnosis](TASK05_DIAGNOSIS.md) for supporting cross-platform evidence,
including unresolved MetricFlow filters, altered aggregate semantics, annotated `BLANK()`
substitutions and conversion failures. The [Task 05 validation record](../results/task05-minimal/validation.json)
records the historical regression and preservation checks. Repeat experiments in new
directories; never overwrite those records.

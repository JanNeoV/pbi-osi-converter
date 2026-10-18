# Semantic interoperability in five minutes

**Can the same business metrics survive conversion between MetricFlow, Power BI
and Databricks using Apache Ossie?** Run the source, change one revenue rule, then
inspect what the official converter actually retains. Installation takes longer
than the demonstration; after setup, all Python commands below run locally.

## Step 1 — Understand the business problem

A fictional shop records [12 sessions](../fixtures/conversion_demo/data/sessions.csv)
and [six orders](../fixtures/conversion_demo/data/orders.csv). Exclude the bot and
internal session. Ten eligible sessions remain; four distinct sessions have a paid
order. Cancelled and refunded orders do not count as conversions.

| Headline metric | Direct | Indirect | Overall |
|---|---:|---:|---:|
| Conversion Rate | **50%** (3/6) | **25%** (1/4) | **40%** (4/10) |
| Paid Revenue | **300 EUR** | **90 EUR** | **390 EUR** |

The overall rate divides total converted sessions by total eligible sessions.
Averaging 50% and 25% gives 37.5% because it incorrectly gives both channels equal
weight. Sessions without orders must remain in the denominator.

Revenue includes paid orders attached to eligible sessions. Including the existing
50 EUR refunded order changes **390 to 440 EUR**; Conversion Rate stays **40%**
because its paid-order rule does not change. These are measured source results:
[baseline](../results/task05-minimal/metricflow/baseline/source/execution.json),
[edited](../results/task05-minimal/metricflow/include-refunded/source/execution.json).

Both metrics use January/February 2026 **session cohorts**, observing orders before
March 1. A February order from a January session belongs to January. The precise
[contract](../fixtures/conversion_demo/contract.json) is supporting detail.

## Step 2 — Run the original model

Prerequisites: Git, Python **3.12**, and internet access for the one-time pinned
installation. No analytics accounts or credentials are needed. From a clean clone,
use **Windows PowerShell**:

```powershell
git clone https://github.com/JanNeoV/pbi-osi-converter.git
Set-Location pbi-osi-converter
# Before the first merge, select the published research branch:
git switch research/semantic-interoperability-lab
python -m pip install uv==0.12.17
python labs/semantic_interop/scripts/setup.py
$py = '.tmp/semantic-lab-venv/Scripts/python.exe'
& $py labs/semantic_interop/scripts/run_conversion_demo.py --check
```

After this PR is merged, use `main` instead of switching to the research branch.
For an existing checkout, start at the installation commands from its repository
root. No environment activation or PowerShell execution-policy change is required.
The setup script uses the unchanged [lockfile](../uv.lock).

The runner prints the report path under `.tmp/conversion-demo/run-*` and checks
**31 native MetricFlow cases against independent DuckDB SQL**, plus five deliberately
injected SQL faults. The faults are teaching examples, not upstream defects.
Linux/macOS path variants and the scope of platform verification are in the
[offline quickstart](../README.md#offline-quickstart).

## Step 3 — Inspect the results

Open the printed `report.json`, `native/*.sql` and `oracle/*.sql` in that run folder.
For the canonical run, `stage_a` reports `native_cases: 31`, `native_matches: 31`,
`detected_failures: 5`, and `accepted: true`. This includes supporting counts,
channel/month breakdowns and empty slices; the headline story remains the two metrics.
The [sealed native report](../results/task04-showcase/native/report.json) provides
an already captured example.

That source run does **not** execute converted Power BI or Databricks definitions.
Across the [nine official conversion paths](RESULTS.md), eight emit output; one
fails. Returned MetricFlow models cannot initialize because required time-spine
configuration is missing; entities and aggregation-time defaults are also lost.
Power BI and Databricks native round trips retain inspected definitions, but their
engines were not run. Cross-platform exports can retain unresolved filter names,
drop metadata, or substitute `BLANK()` for unsupported SQL-to-DAX expressions.
The [diagnosis](TASK05_DIAGNOSIS.md) links the exact artifacts and warnings.

**There is no numerically verified unchanged conversion happy path.** File output,
static preservation and executed oracle agreement are separate claims.

## Step 4 — Change one metric

This uses a disposable copy of the canonical dbt project. Choose a fresh folder on
repeat; the helper refuses to overwrite evidence.

```powershell
$work = '.tmp/task06-five-minute'
$helper = 'labs/semantic_interop/showcase/metricflow_task05.py'
& $py $helper init --work $work
```

Open `.tmp/task06-five-minute/dbt/models/metrics.yml`. Under
`name: paid_revenue_base`, replace its **entire two-line `filter` field, including
the continuation line**, with the single line below. The source uses single-quoted
YAML with doubled inner quotes; replacing the complete field avoids quote errors.
The only business change is expanding the paid status to paid or refunded:

```yaml
  filter: "{{ Dimension('session__is_eligible') }} = true AND {{ Dimension('order__order_status') }} IN ('paid', 'refunded')"
```

Keep the conversion metric and its dependencies unchanged. Rebuild and execute:

```powershell
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$work/dbt" --profiles-dir "$work/dbt"
& $py $helper execute --work $work --label after --include-refunded
```

Open `$work/after/execution.json`: all four `equal` values should be true. Paid
Revenue becomes **440 EUR** (Direct 300, Indirect 140); conversion stays **40%**
(Direct 50%, Indirect 25%). `--include-refunded` chooses the independent comparison
oracle; the YAML edit changes the model. [Exercise A](TUTORIAL.md#exercise-a-metricflow-include-refunded-orders)
includes the before run, return attempt, troubleshooting and reset.

## Step 5 — Try the official Ossie conversion

Use the installed official `apache-ossie-dbt` CLI; leave its warnings visible:

```powershell
$dbtAdapter = '.tmp/semantic-lab-venv/Scripts/ossie-dbt.exe'
& $dbtAdapter msi-to-ossie -i "$work/dbt/target/semantic_manifest.json" -o "$work/after.ossie.yml" --model-name conversion_demo
& $dbtAdapter ossie-to-msi -i "$work/after.ossie.yml" -o "$work/after.returned.json"
& $py $helper execute --work $work --label returned --manifest "$work/after.returned.json" --include-refunded
```

Open `after.ossie.yml` and find `IN ('paid', 'refunded')`. The changed text survives,
but the filter names remain unresolved logical identifiers. Open
`after.returned.json` and `returned/execution.json`: the unchanged return lacks the
time spine and cannot execute. **440 EUR was measured in the edited source; it is
not a converted-target result.** No generated model is silently repaired.
The [Task 05 converted artifacts and console captures](../results/task05-minimal/metricflow/include-refunded/)
retain this exact finding. To inspect its Power BI export locally:

```powershell
$msAdapter = '.tmp/semantic-lab-venv/Scripts/ossie-microsoft.exe'
& $msAdapter export -i "$work/after.ossie.yml" -o "$work/after.model.bim"
```

The output emits `BLANK()` for Paid Revenue and retains original SQL as an
annotation; the warnings explain the unsupported expressions. Retained text does
not calculate the metric. Reset your edited copy with:

```powershell
Copy-Item -LiteralPath labs/semantic_interop/fixtures/conversion_demo/dbt/models/metrics.yml -Destination "$work/dbt/models/metrics.yml"
& $py -m dbt.cli.main build --no-partial-parse --project-dir "$work/dbt" --profiles-dir "$work/dbt"
& $py $helper execute --work $work --label restored
```

The restored source returns **390 EUR / 40%**. Continue with the independent
[MetricFlow, Power BI and Databricks exercises](TUTORIAL.md), the
[nine-path results and limitations](RESULTS.md), or
[Charlotte's Databricks handoff](DATABRICKS_HANDOFF.md).

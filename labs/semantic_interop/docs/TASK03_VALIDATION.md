# Task 03 validation and evidence custody

The [final validation record](../results/task03/validation.json) records measured
commands, results, preservation checks and execution boundaries. The
[file inventory](../results/task03/file-changes.json) lists the additions and the
three intentionally changed original files. Work remains local and unpublished.

## Stage A acceptance

The [gate record](../results/task03/stage-a-validation.json) precedes Stage B.
At that gate, all 66 lab tests passed, the original Task 02 diagnostics passed
twice offline with identical reports, and historical evidence hashes matched.
Broader parent checks continued independently and ultimately passed as recorded
below. Stage B did not replace the required MetricFlow execution with SQL models.

- [Canonical capture](../results/task03/stage-a-001/report.json): 31 actual
  MetricFlow-generated queries execute and match independent raw-table SQL.
- [Additional-order capture](../results/task03/edge-001/report.json): another
  31/31 match; conversion remains 40%, revenue becomes EUR 420, paid orders 5,
  and AOV EUR 84.
- All five injected SQL faults execute and are detected. They are teaching
  controls, not converter defects.
- Cutoff and eligibility tests re-execute unchanged generated SQL after adding
  a paid order exactly at the cutoff and an excluded-session paid order; the
  canonical results remain unchanged. The mutations are rolled back.

The source explicitly uses supported MetricFlow derived expressions to retain
eligible cohort groups with no paid activity. The independent SQL oracle neither
imports those expressions nor computes expected values from converter output.
The canonical cohort is January 1 inclusive to March 1 exclusive, with orders
observed before March 1. Session-cohort revenue is EUR 270/120; transaction-month
revenue is separately tested as EUR 190/200.

## Stage B execution boundary

The [official-conversion capture](../results/task03/platforms-001/report.json)
was repeated twice under the inherited Python socket guard with identical reports.
Its 31 native cases match. Official MSI→Ossie and Ossie→MSI conversion returns
parseable artifacts, but missing time-spine configuration blocks all 31 requested
round-trip numerical comparisons. Source entity and default-time metadata are
also absent in the return inventory. No repair or configuration reconstruction
is applied to this new experiment.

The official Microsoft export emits nine `BLANK()` definitions: six public metrics
and three base helpers. Its one input Ossie relationship yields no Microsoft
relationship. These are observed output structures, not Power BI numerical failures.
Power BI/DAX execution and TOM validation are `NOT_EXECUTED`.

The [authored Power BI](../fixtures/conversion_demo/targets/powerbi/README.md) and
[Databricks](../fixtures/conversion_demo/targets/databricks/README.md) definitions
have separate provenance and prospective target-engine queries. Local TMDL inventory
and YAML parsing do not validate either target runtime. The official Databricks
Java converter was not executed: no ready JAR/toolchain was supplied. No custom
converter, cloud account, deployment, or DAX interpreter was introduced.

## Final checks

Run these from the repository root using the existing pinned lab Python:

```text
python labs/semantic_interop/scripts/run_conversion_demo.py --check
python labs/semantic_interop/scripts/run_conversion_demo.py --check --edge
python labs/semantic_interop/scripts/run_conversion_platforms.py
python -m pytest -q labs/semantic_interop/tests
python -m pytest -q -m "not live_agent"
python labs/semantic_interop/scripts/check_task02.py --upstream-python .tmp/task02-replay/current-env/Scripts/python.exe --repair-python .tmp/task02-replay/repair-env/Scripts/python.exe
python scripts/check_public_repository.py --after-demo
```

Final observed results: **78 lab tests passed; 165 parent tests passed, one skipped**.
All original audit/demo commands, including the public demonstration, passed.
The final Task 02 wrapper again produced two identical offline reports and verified
their artifacts. The new Stage B reports were also identical across two guarded
runs. The guard denies Python socket connection/name-resolution audit events; it
is not an operating-system firewall. Remote CI and Linux were `NOT_EXECUTED`.

## Preservation

The [starting hash inventory](../results/task03/baseline.json) covers 1,184 original
files including supplemental original public-checker/provenance hashes. Every
historical result retains its original bytes; 203 artifacts across the three new
captures were independently hash-verified. Dependency versions and lockfiles are
unchanged. No upstream checkout or converter code was modified.

The initial `check.py` run exposed a compatibility conflict: the root README is
hash-bound by the public checker, while `PUBLIC_PROVENANCE.json` itself is protected
by Task 02. The original provenance file was restored byte-for-byte. A separate
[public revision record](../../../docs/PUBLIC_REVISIONS.json) now binds old and
current hashes for the README and checker only. All other export hashes remain
mandatory. Twelve focused tests reject wrong hashes, unsupported revision targets
and malformed metadata. All `check.py` component checks subsequently passed.

Only the root README, lab README and public checker changed among original files.
The old fixtures, original report schema, accepted Task 01/02 reports, package pins
and original article remain unchanged. Captures reuse the existing hash-verifying
routine and refuse existing destinations. No push, merge, publication or deployment
was performed.

## Task 04 continuation: minimal showcase and Charlotte handoff

The Task 03 acceptance record above and its captured results are historical and
unchanged. Task 04 updates the [five-minute guide](CONVERSION_DEMO.md) and
[article](CONVERSION_ARTICLE_DRAFT.md), and adds the
[Databricks handoff](DATABRICKS_HANDOFF.md). Its separate
[validation record](../results/task04/validation.json) and
[changed-file inventory](../results/task04/file-changes.json) contain exact commands,
output paths, current upstream review and preservation checks.

The showcase exposes only Conversion Rate and Paid Revenue. Its fresh native
headline run has 11/11 oracle matches; the full internal gate retains 31/31 and
5/5 detected controls. The unchanged official MetricFlow return still has zero
numerical executions. Two Power BI-origin round trips preserve bounded inspected
properties through DAX and Power BI extensions; TOM/DAX execution remains
`NOT_EXECUTED`. The separately built current official Databricks Java converter
passes 100 upstream tests and exports/reimports the actual Ossie output. Its
warnings, unresolved identifiers, and missing cohort-month dimension are retained;
Databricks analysis, numerical execution and oracle comparison remain
`NOT_EXECUTED`.

The 78 lab regressions pass. [Task 02](../results/task04/task02-final.json) passes
twice offline with identical normalized reports and 305 verified artifact hashes
per run. An [initial equality rejection](../results/task04/task02-initial-difference.json)
records the new Power BI runner appearing/changing in implementation fingerprints
during concurrent work; query results and individual acceptance checks were
unchanged. The stable-source retry is the final acceptance evidence.

All 1,301 historical result files remain byte-identical to the Task 04 starting
snapshot. Fourteen historical hash manifests verify 1,871 references to 1,250
unique artifacts. The original Task 02 archive hash and all 75 baseline package
versions remain unchanged. Final parent/public checks and inventory totals are
recorded in the Task 04 validation JSON. No upstream implementation was edited;
no push, merge, publication or deployment was performed.

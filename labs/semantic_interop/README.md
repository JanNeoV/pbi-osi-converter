# Semantic Interoperability & Correctness Lab

Can semantic definitions move between MetricFlow, Power BI and Databricks using Apache Ossie while
retaining their business meaning and numerical results?

This small synthetic benchmark extends the parent project's static conversion
assurance with independent DuckDB reference queries and real upstream tool execution.
It separates structural validity, bounded semantic checks and numerical agreement.
It is not a general equivalence proof or a production converter.

## Start with three interoperability experiments

Use the [three-experiment quickstart](showcase/QUICKSTART.md) for the frozen
12-session/six-order example. Its only headline metrics are Conversion Rate
(Direct 50%, Indirect 25%, overall 40%) and Paid Revenue (EUR 300/90/390).
January and February use session cohorts, including the cross-month order.

The [nine-path results](showcase/RESULTS.md)
separate completed conversion, static preservation, target execution and oracle agreement.
The [hands-on tutorial](showcase/TUTORIAL.md) has independent MetricFlow, Power BI
and Databricks exercises. The [Databricks handoff for Charlotte](showcase/DATABRICKS_HANDOFF.md) contains exact
inputs, pins, prerequisites and commands. The
[acceptance record](showcase/ACCEPTANCE.md) separates actual validation from unverified platforms.
Existing Task 02 evidence and Task 03 accepted results remain immutable.

## Historical measured milestone

This section describes the earlier 14-query research fixture, not the canonical
31-case sessions/orders teaching fixture or the nine-path showcase above. Keep
its counts separate from the current [results](showcase/RESULTS.md).

- **14/14 native MetricFlow query cases pass**: eight business queries and six edge slices.
- The official dbt/Ossie round trip runs, but all 14 execution attempts are blocked
  by missing time-spine configuration. Static checks also detect loss of declared
  entities/default time dimensions and altered filtered-metric definitions.
- The official Microsoft converter emits **four annotated `BLANK()` measures**
  and skips **two relationships**. Two other measure expressions are emitted;
  neither has Power BI runtime evidence.
- **10/10 controlled failures are detected**, including **six numerical mismatches**.
  These injected SQL faults are not claimed as upstream defects.

The checked [JSON evidence](results/evidence/report.json) contains 51 records:
14 `PASS`, 16 `SEMANTIC_MISMATCH`, six `NUMERICAL_MISMATCH`, nine `UNSUPPORTED`,
five `NOT_EXECUTED`, and one `INVALID_MODEL`. Final status and execution status are
separate: the 14 round-trip semantic mismatches were **not numerically executed**.
Do not interpret this mixture of queries, metadata checks and deliberate faults as
an overall converter pass rate.

## Offline quickstart

Requires Git, Python **3.12** and pinned uv **0.12.17**. Installation downloads
public dependencies; the benchmark runs locally without credentials or network.
From the repository root in Windows PowerShell:

```powershell
python -m pip install uv==0.12.17
python labs/semantic_interop/scripts/setup.py
$py = '.tmp/semantic-lab-venv/Scripts/python.exe'
& $py labs/semantic_interop/scripts/run_conversion_demo.py --check
```

Read the printed report path under `.tmp/conversion-demo/run-*`. Expected:
**31 native cases match the independent oracle; five injected faults are detected**.
The [three-experiment quickstart](showcase/QUICKSTART.md) explains the two headline values and
shows the verified revenue edit from 390 to 440 EUR. These commands use the
frozen [lockfile](uv.lock); do not upgrade it to reproduce the evidence.

The scripts use portable Python paths and CI is configured for Windows and Ubuntu.
On Linux/macOS the equivalent commands are:

```sh
python3 -m pip install uv==0.12.17
python3 labs/semantic_interop/scripts/setup.py
.tmp/semantic-lab-venv/bin/python labs/semantic_interop/scripts/run_conversion_demo.py --check
```

These shell variants are provided for the portable scripts. Task 06 was executed
on Windows; Linux/macOS execution is not claimed as locally verified. See the
[validation record](docs/TASK06_VALIDATION.md) for the actual test scope.

For all lab tests, original public demos, parent tests and repository hygiene:

```powershell
& $py labs/semantic_interop/scripts/check.py
```

Troubleshooting: run commands from the repository root; use the pinned environment's
Python, not a global interpreter. If `uv` is missing after installation, reopen the
shell so its scripts directory is on `PATH`. Setup needs network access; execution
does not. Expected converted-model blockers are documented in [RESULTS.md](showcase/RESULTS.md).
Power BI Desktop and Databricks cloud execution are separate manual prerequisites,
not part of this offline gate.

## Historical setup and execution

### Task 02: semantic-loss investigation

The original measured milestone and `results/evidence/` remain historical evidence.
Task 02 adds a [sealed report](results/task02/verified-003/report.json),
[generated comparison tables](docs/DIFFERENTIAL_EVALUATION.md),
[root-cause diagnosis](docs/ROUND_TRIP_DIAGNOSIS.md), and an [article draft](docs/ARTICLE_DRAFT.md).
The [validation and custody record](docs/TASK02_VALIDATION.md) links final checks and the file inventory.
The unchanged pinned/current round trip still cannot execute. A separately installed
entity-key repair **plus explicitly reconstructed time-spine/default configuration**
executes 11/14 cases, all matching the oracle; three filtered-revenue cases remain blocked.
This is not a lossless round trip. Microsoft runtime evidence remains NOT_EXECUTED.

Use the pinned lab Python from the repository root:

```text
python labs/semantic_interop/scripts/setup_task02.py
```

Setup acquires the frozen current source and applies the retained patch in a separate
checkout under `.tmp/task02-replay`. It refuses an existing destination. It never upgrades
the baseline environment. On Windows:

```powershell
.\.tmp\semantic-lab-venv\Scripts\python.exe labs/semantic_interop/scripts/check_task02.py --upstream-python .tmp/task02-replay/current-env/Scripts/python.exe --repair-python .tmp/task02-replay/repair-env/Scripts/python.exe
```

On Linux/macOS, replace `Scripts/python.exe` with `bin/python`. The check runs twice
under the offline guard and verifies hashes and deterministic evidence. For a single run,
use `python -m semantic_lab diagnose --check --upstream-python PATH --repair-python PATH`.
`--capture NEW_DIRECTORY` stores a portable bundle and refuses overwrites. Omitting the
current environment makes Task 02 acceptance fail; it does not trigger a download.

Minimal cases run without dbt build or the full benchmark:

```text
python labs/semantic_interop/scripts/reproduce_semantic_loss.py entity_alias
python labs/semantic_interop/scripts/reproduce_semantic_loss.py compound_aggregate
python labs/semantic_interop/scripts/reproduce_semantic_loss.py metric_filter
```

These execute the captured one-row source and converted manifest with the official
MetricFlow renderer and DuckDB, retaining explicit reconstruction ledgers. With the
repair environment, `entity_alias --expect match` verifies the corrected execution.
See the [upstream review](../../docs/UPSTREAM_COMPATIBILITY_REVIEW.md) and
[local contribution candidate](../../docs/OSSIE_CONTRIBUTION_CANDIDATE.md).

### Original milestone commands

Requires Git, Python 3.12 and uv 0.12.17. Installation downloads public pinned
dependencies; benchmark execution requires no accounts, credentials or network.
Run setup from the repository root:

```text
python -m pip install uv==0.12.17
python labs/semantic_interop/scripts/setup.py
```

Windows PowerShell:

```powershell
.\.tmp\semantic-lab-venv\Scripts\Activate.ps1
Set-Location labs/semantic_interop
python -m semantic_lab benchmark --check
```

Linux/macOS:

```sh
. .tmp/semantic-lab-venv/bin/activate
cd labs/semantic_interop
python -m semantic_lab benchmark --check
```

Activation is optional for the benchmark: invoke the environment's Python directly
if shell activation is unavailable. From the lab directory the Windows executable
is `../../.tmp/semantic-lab-venv/Scripts/python.exe`; on Linux it is
`../../.tmp/semantic-lab-venv/bin/python`.

The command prints the repository-relative report location under
`.tmp/semantic-lab/run-*`. Every run uses a fresh generated dbt project and database.
`--check` fails if the required native cases, integrations or failure detections fail;
it does not require intentionally faulty cases to become correct. Without `--check`,
the runner produces a diagnostic report even when milestone acceptance fails.

From the repository root, using the environment's Python:

```text
python labs/semantic_interop/scripts/check.py
```

This runs the lab tests, original audit/demos, parent regression suite and public
integrity checks with the correct console scripts on PATH. It does not publish or
deploy anything. Retain all checked historical bundles: the original maintainer
`scripts/capture_evidence.py` refreshes accepted evidence and is not a reproduction
step. For a new Task 02 record use `diagnose --capture NEW_DIRECTORY` instead.

## Layout and reading order

- `fixtures/`: maintained synthetic data, dbt YAML/models, independent SQL oracle,
  fault SQL, bounded contract and manually authored TMDL.
- `src/semantic_lab/`: loader, typed comparator, thin upstream adapters, static checks
  and evidence runner. No model-generated SQL and no DAX interpreter.
- `results/evidence/`: captured report, generated SQL, converter outputs and diagnostics.
- [Repository decision](../../docs/REPOSITORY_DECISION.md),
  [architecture](docs/ARCHITECTURE.md), [benchmark specification](docs/BENCHMARK_SPEC.md),
  [test matrix](docs/TEST_MATRIX.md), [upstream audit](docs/UPSTREAM_AUDIT.md),
  [limitations](docs/LIMITATIONS.md), [article outline](docs/ARTICLE_OUTLINE.md),
  [follow-up backlog](docs/BACKLOG.md).

The package and original code remain Apache-2.0 under the repository's
[license](../../LICENSE). The lab does not install Microsoft TOM; its optional
Microsoft-licensed setup is described separately in the upstream audit.

### Task 03 and Task 04 continuation

The [Task 03 validation record](docs/TASK03_VALIDATION.md) retains the accepted
benchmark and links the Task 04 continuation. Use the
[walkthrough](docs/CONVERSION_DEMO.md) for current showcase results and official
round-trip boundaries; generated outputs and authored target candidates have
separate provenance. The later [Power BI runtime supplement](showcase/POWERBI_RUNTIME_VALIDATION.md)
retains actual DAX results for one unmodified core roundtrip. Historical records
remain unchanged; Databricks target execution is still unverified.

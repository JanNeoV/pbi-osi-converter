# Task 06: publication and Charlotte handoff

Date: October 18, 2026. Scope: documentation, reproducibility, publication QA and
handoff of the existing lab. No converter, canonical fixture, research definition
or dependency pin changes are part of this task.

## Reading order

The [root README](../../../README.md) retains the separate Power BI/Snowflake
assurance project and exposes the lab. Start with the
[five-minute demonstration](../showcase/README.md), install using the
[offline quickstart](../README.md#offline-quickstart), then choose an independent
[tutorial exercise](../showcase/TUTORIAL.md). The single
[nine-path matrix](../showcase/RESULTS.md) separates file conversion, bounded static
preservation, target execution and numerical oracle agreement.
[Charlotte's handoff](../showcase/DATABRICKS_HANDOFF.md) starts from the eventual
merged `main`, records the actual merge SHA, and supplies the existing inputs,
converter pins, SQL and result template. No future merge SHA is invented.

## Historical custody

The Task 05 [baseline](../results/task05-minimal/baseline.json) remains the original
1,865-file preservation mechanism. It binds historical results, canonical fixtures
and dependency files to SHA-256. All **1,865 files remain unchanged in the original
author workspace**. The installed
environment still matches its **77 package records**, and all **17 installed
converter module hashes** match the Task 04 installed-source audit.

The Task 05 [artifact manifest](../results/task05-minimal/artifact-sha256.json) is
also retained unchanged. Its current documentation entries describe the historical
Task 05 versions; the current showcase README, tutorial and handoff are intentionally
revised in Task 06. Its **316 evidence/model artifact hashes** remain unchanged.
Do not regenerate historical manifests to hide current documentation revisions.
The Task 06 [preservation record](../results/task06-publication/preservation.json)
records these separate checks.

The baseline also includes **five ignored `.egg-info/` build-metadata files**.
They are not published inputs or dependencies. A fresh editable installation
regenerates them; four happened to match the historical bytes, while `SOURCES.txt`
correctly lists the later lab modules/tests. The original full-baseline check
therefore reported that one metadata difference in the fresh clone. The manifest
and original workspace metadata remain unchanged. Do not commit or copy stale
generated metadata to manufacture a clean-clone match.

All **1,860 tracked historical files** match their original hashes in the fresh
Git clone. To repeat that portable check from the repository root in PowerShell:

```powershell
@'
from pathlib import Path
import hashlib, json
baseline = Path('labs/semantic_interop/results/task05-minimal/baseline.json')
files = json.loads(baseline.read_text(encoding='utf-8'))['files']
generated = {name for name in files if '.egg-info/' in name}
portable = {name: digest for name, digest in files.items() if name not in generated}
changed = [name for name, expected in portable.items()
           if not Path(name).is_file()
           or hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected]
print(json.dumps({'historical_records': len(files), 'portable_files': len(portable),
                  'generated_metadata_excluded': sorted(generated),
                  'changed_or_missing': changed}))
assert len(files) == 1865 and len(generated) == 5 and len(portable) == 1860 and not changed
'@ | & .tmp/semantic-lab-venv/Scripts/python.exe -
```

`PUBLIC_PROVENANCE.json` is immutable. The existing narrow
[`PUBLIC_REVISIONS.json`](../../../docs/PUBLIC_REVISIONS.json) mechanism binds the
current README/checker bytes to their original exported hashes. The checker now
resolves exactly one historical README snapshot from its original document
location while still checking every link. Its regression tests reject genuinely
broken snapshot links and prevent the rule applying to other files. CommonMark
angle-bracket destinations allow fixture filenames with spaces while retaining
repository-boundary validation.

## Executed checks

All Task 06 execution is on Windows with the pinned Python 3.12 environment.
Linux/macOS shell variants use portable script paths; their execution is not
claimed as locally verified. Power BI Desktop/DAX and Databricks native execution
remain manual and unverified.

The [machine-readable validation record](../results/task06-publication/validation.json)
records exact commands and results. Relevant commands from the repository root:

```powershell
$env:PATH = "$(Resolve-Path .tmp/semantic-lab-venv/Scripts);$env:PATH"
& .tmp/semantic-lab-venv/Scripts/python.exe -m pytest -q labs/semantic_interop/tests --basetemp .tmp/pytest-task06-lab
& .tmp/semantic-lab-venv/Scripts/python.exe -m pytest -q -m "not live_agent" --basetemp .tmp/pytest-task06-parent
& .tmp/semantic-lab-venv/Scripts/python.exe labs/semantic_interop/scripts/check_task02.py --upstream-python .tmp/task02-replay/current-env/Scripts/python.exe --repair-python .tmp/task02-replay/repair-env/Scripts/python.exe --capture .tmp/task06-task02-replay
& .tmp/semantic-lab-venv/Scripts/python.exe scripts/check_public_repository.py --after-demo
git -c core.whitespace=trailing-space,space-before-tab,cr-at-eol diff --cached --check -- README.md scripts/check_public_repository.py tests/test_readme_revision.py .github/workflows/semantic-lab.yml docs labs/semantic_interop/docs labs/semantic_interop/showcase labs/semantic_interop/README.md
```

| Check | Task 06 result |
|---|---|
| Lab regression suite | 84 passed |
| Public parent suite, `not live_agent` | 171 passed, 1 skipped |
| Captured audit, three public demos, narrated wrapper | All five exit 0 |
| Checker-specific regressions | 18 passed; included in parent count |
| Task 02 offline replay | Two identical reports; all artifact hashes verified |
| Isolated setup and benchmark | 31/31 matches; 5/5 injected faults detected |
| Isolated environment's lab suite | 84 passed |
| Fresh pinned Java build | 100 tests, zero failures/errors/skips |
| Fresh Git clone | Setup, 31-case benchmark and public hygiene pass; 1,860 tracked historical hashes match |

Counts describe different checks and are not combined into an interoperability
pass rate. Whitespace validation permits preserved CRLF endings. Captured results,
patches and canonical fixtures retain their original whitespace and hashes; the
full-diff audit records those archival findings separately from current source
and documentation. Three unchanged historical files (`pyproject.toml`, `setup.py`,
and the package `__init__.py`) also retain a blank line at EOF. The current
publication code/documentation check passes. No formatting cleanup is applied to
accepted evidence or pinned dependency files; see the
[complete whitespace audit](../results/task06-publication/whitespace-audit.json).

Task 06 additionally repeated installation into an isolated source export with
no copied environment: the source benchmark matched **31/31** cases, detected
**5/5** injected faults, and the fresh environment passed all **84 lab tests**.
The three manual exercises were replayed in new work directories: MetricFlow
baseline/edit/reset matched their independent oracles; all eleven Power BI
bounded checks passed for each candidate version; both native Databricks YAML
comparisons passed. The [manual replay record](../results/task06-publication/manual-replay.json)
and retained `metricflow-*.execution.json` files distinguish those measurements
from the unchanged returned MetricFlow initialization blocker.

A fresh pinned Java checkout and empty isolated Maven cache produced **100 tests,
zero failures/errors/skips**, and the same JAR SHA-256 as the historical build.
The new JAR's export also matched the captured baseline YAML. The
[build record](../results/task06-publication/java-build.json) retains commands,
versions, hashes and all observed build warnings; the
[Surefire summaries](../results/task06-publication/java-test-results.txt) retain
the four actual suite outcomes. No Databricks engine was involved.

The [fresh Git-clone record](../results/task06-publication/clean-checkout.json)
identifies the checked source commit, exact commands and the generated-metadata
finding above. Its benchmark/public checks succeed with a newly created environment
and leave that checkout clean. The follow-up commit updates validation documentation
and records only.

The Task 02 replay requires the separate pinned environments created by
[`setup_task02.py`](../scripts/setup_task02.py); it does not use the baseline
environment as the repaired converter. Both repeated reports matched with the
Python offline socket guard active and all artifact hashes checked. This verifies
the historical 14-query experiment separately from the 31-case teaching fixture.

## Publication boundaries

The complete publication inventory includes previously untracked Tasks 01–05 work;
the [changed-file inventory](../results/task06-publication/changed-files.txt)
lists exact PR paths. Ignored environments, downloaded tools/JARs, runtime databases
and temporary execution trees stay under `.tmp/`. Portable historical evidence is
retained; no execution environment is committed. Generic repository paths inside
old command logs and model bindings are evidence, not private credentials or an
assumption for the new quickstart.

The branch is prepared for human review targeting `main`. It is not merged or
deployed by this task. Power BI candidates remain **NOT_VERIFIED**. Charlotte can
perform offline conversion after installing Java/Maven and can attempt native
queries only with an authorized compatible Databricks environment. Generated
models remain unchanged; a manually authored control must be reported separately.

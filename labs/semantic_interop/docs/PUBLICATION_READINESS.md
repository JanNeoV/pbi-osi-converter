# Publication readiness — runtime closeout, 2026-10-21

**READY for the bounded runtime claim.** The native Power BI core and unmodified
official Ossie return loaded, refreshed and executed in Desktop 2.159.883.0.
Both match independent DuckDB SQL: overall **0.4 / 390 EUR**, Direct **0.5 / 300 EUR**,
Indirect **0.25 / 90 EUR**. Actual queries, numerical rows, versions, hashes and
reproduction steps are in the [runtime report](../showcase/POWERBI_RUNTIME_VALIDATION.md)
and its [public evidence package](../showcase/runtime-evidence-20261020/provenance.json).
All 159 retained raw capture hashes matched; originals remain local and unchanged.

This adds one bounded unmodified Power BI roundtrip to the verified native
MetricFlow source result. It does not establish cross-platform portability.
MetricFlow's native return still fails initialization; MetricFlow → Power BI
has a static definition mismatch and an observed opening failure, with nine
`BLANK()` measures unexecuted. Selector originals remain FAIL; minimal corrected
copies are separately labeled REPAIRED / EXPERIMENTAL. Both baseline repairs and
the returned changed-default repair passed DAX checks; the native changed-default
repair loaded but failed refresh with a cyclic-reference error. Card rendering
is untested.

The `te --help` probe failed because no CLI was found. No preview dependency was
introduced. See the runtime report for the tooling assessment and selector limits.
Private editorial files remain ignored; historical snapshots and converter pins
are preserved. The dated offline closeout below is retained as historical context.

## Offline publication closeout - 2026-10-20

**Verdict: READY for the bounded article.** The three documented experiments and
both existing edit exercises were reproduced in a clean Python 3.12 environment.
At that offline closeout, no converted target had verified numerical equivalence. The article and private
publication notes remain ignored and untracked.

## Fresh local verification

A clean local Git clone of review branch `b73b3519` was installed with Python
3.12.10 and `uv==0.12.17` on Windows. The new virtual environment used the frozen
lockfile; dependency downloads reused the package cache. No environment or private
draft was copied into the clone. Official Ossie adapters remain pinned to
`6ebc5e5612467dac405985f44ea078b39c891bee`; no converter or returned model was repaired.

| Experiment or edit | Executed result | Static preservation | Target runtime / numerical agreement |
|---|---|---|---|
| MetricFlow -> Ossie -> MetricFlow | Both conversions PASS; source 31/31 oracle matches | MISMATCH: missing time spine, entities and aggregation-time defaults; ratio dependencies rewritten | 11 attempts BLOCKED first at missing-spine initialization; no target values |
| Power BI -> Ossie -> Power BI (core) | Both converters PASS | All nine bounded checks PASS, including DAX, dependencies, relationships, columns, partitions, M and formats | Desktop NOT_EXECUTED; numerical agreement unverified |
| MetricFlow -> Ossie -> Power BI | Both converters PASS | Nine annotated `BLANK()` substitutions, including Conversion Rate; 20 warnings including a skipped relationship | Desktop NOT_EXECUTED; definition mismatch, not an observed numerical failure |
| MetricFlow refund edit | Four source comparisons PASS before and after: revenue 390 -> 440 EUR, channels 300/90 -> 300/140; conversion remains 40%, channels 50%/25% | Expanded predicate retained in Ossie; Power BI export still has nine annotated blanks | Unchanged MetricFlow return BLOCKED at missing spine; no converted-target values |
| Power BI selector default edit | Baseline and changed candidates each complete official import/export in PowerShell and Git Bash | All 11 bounded checks PASS in each run, including dependencies and dynamic formatting; only the two alternate-result defaults differ | Desktop NOT_EXECUTED; selection and card behavior remain manual acceptance criteria |

The source oracle is independently written SQL over raw fixture data. Both source
and oracle queries execute in DuckDB. The five deliberately injected SQL faults
were detected; they are controls, not observed converter output. The hypothetical
37.5% channel-rate average is not an observed target result.

Fresh commands/checks:

- Frozen setup and `run_showcase.py --scope three --check --output .tmp/three-demo`:
  PASS, 31 source matches and five detected controls.
- `python -m pytest -q labs/semantic_interop/tests --basetemp .tmp/closeout-tests`:
  **92 passed**, including focused offline tests (109.41 seconds).
- `python -m pytest -q tests/test_readme_revision.py tests/test_public_contract.py`:
  **27 passed** (51.47 seconds).
- Documented refund init/build/execute, official outbound/return conversion,
  unchanged returned execution attempt, and Power BI export: results above.
- Documented selector import/export: both variants in PowerShell and Git Bash;
  full normalized model comparisons passed for all four runs.
- All four POSIX shell blocks pass `bash -n`. The selector loop executed in Git
  Bash on Windows with only `bin/python` changed to `Scripts/python.exe`.
  This is not fresh execution on Linux or macOS; WSL has no installed distribution.
- Public repository/link checker, changed-document anchors, generated-report
  links, article URLs and Git whitespace checks: PASS. All 18 local links in the
  freshly generated three-experiment report resolve.

Fresh evidence is retained locally under `.tmp/publication-closeout/checkout/.tmp/`:
`three-demo/`, `refund-exercise/`, `pbi-powershell-{baseline,changed-default}/`,
`pbi-selector-{baseline,changed-default}/`, test logs, and
`closeout-verification.json`. These ignored files do not replace sealed evidence.
Readers can generate their own records using the [quickstart](../showcase/QUICKSTART.md).

## Five tutorial findings and disposition

1. **Selector discovery:** the quickstart now links the separate offline selector
   diagnostic; the standard three-route command uses the core Power BI model.
2. **POSIX commands:** the two candidate variants now have shell commands. The
   assertion step exits on failure, preventing a later variant from masking it.
   Windows shell execution is verified; native macOS/Linux coverage is CI only.
3. **Report interpretation:** exit 0 means the evidence checks passed, including
   the expected blocked target. It does not mean lossless conversion.
4. **Platform coverage:** the dated acceptance addendum distinguishes the passing
   focused three-OS workflow from the failing historical Ubuntu replay.
5. **Desktop boundary:** Desktop is installed locally but was not launched or
   tested in this closeout. No source/returned refresh, DAX query, slicer, or card
   result is claimed. [Tutorial B2/B3](../showcase/TUTORIAL.md#b2-add-and-test-the-dynamic-selector)
   supplies no/single/multiple selection, channel-filter and formatting checks.

## CI status and merge policy

The [focused three-experiment workflow](https://github.com/JanNeoV/pbi-osi-converter/actions/runs/39160283961)
passed on Windows, macOS and Ubuntu for base `6f342299`. This is historical CI,
not a fresh manual run on those operating systems.

The [older full lab workflow](https://github.com/JanNeoV/pbi-osi-converter/actions/runs/39160284146)
failed only its Ubuntu Task 02 replay; Windows passed. Logs show the base Python
interpreter failing to import `semantic_lab`. The replay wrapper and diagnostic
worker resolve virtual-environment executable symlinks before launching them,
which bypasses the installed environment on Linux. No historical repair or replay
is included here. The review-branch [Ubuntu job](https://github.com/JanNeoV/pbi-osi-converter/actions/runs/39203682765/job/117901805453)
also passed the regression step and failed at that historical replay.

Authenticated GitHub branch metadata reports `main` as unprotected, with status
check enforcement off and no required contexts; the repository ruleset listing,
including inherited rulesets, is empty. Thus the historical replay is not a
required merge check. **The whole repository is not green.** Closeout nevertheless
requires the focused three-OS workflow and public offline proof to pass on the
final PR head. Their final results belong to the PR checks, not the older runs.

## Preservation and editorial boundaries

All 2,169 tracked fixture/evidence/pin paths checked against `main` are unchanged;
the fresh hash inventory is retained in the local verification JSON. Of 1,860
portable entries in the older Task 05 snapshot, 1,858 still match. The two other
entries are `showcase.py` and `showcase_powerbi.py`, changed by the already-merged
three-experiment implementation (`019ac8b`); both match current `main` exactly.
No new historical mismatch was introduced, and no snapshot was regenerated.
The three older tracked editorial files and both private ignore rules are unchanged.

The private article is approximately 700 words and uses only Conversion Rate and
`SWITCH` as its examples. The earlier
[Snowflake Semantic Views article](https://medium.com/@jan.busse/maintaining-power-bi-semantic-models-as-snowflake-semantic-views-36cb4bcd9d56)
describes targeted translation rules and explicit business decisions. The new
article examines interchange and adapter boundaries; structural preservation is
not a claim that vendor-specific expressions translate into executable equivalents.
Article text and private notes must remain outside commits, PRs and CI artifacts.

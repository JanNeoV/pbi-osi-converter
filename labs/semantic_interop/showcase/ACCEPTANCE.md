# Three-experiment acceptance record

Validated on **2026-10-18**, Windows 11, Python 3.12.10, against base `6bb0457` plus
the implementation before its subsequent Git publication. This is a local
validation snapshot; it includes no remote CI or Medium-publication result. [Machine-readable record](ACCEPTANCE.json) retains commands,
versions, statuses, warnings, output paths and evidence hashes.

| Check | Actual result |
|---|---|
| Complete existing check.py suite | PASS; all nine command groups exit 0 |
| Complete suite in isolated checkout | PASS; all nine command groups exit 0 |
| Lab tests | 92 passed in 104.20s (0:01:44) |
| Parent regression tests | 173 passed, 1 skipped in 842.75s (0:14:02) |
| Focused offline demo | 31/31 source matches; 5/5 injected faults detected |
| Isolated pinned installation | PASS; new virtual environment, no copied environment |
| Final implementation in isolated checkout | All three paths accepted; classifications match local offline run |
| Historical Task 02 replay | PASS; repeated reports equal and artifact hashes verified |
| Optional refund exercise | Source 390 → 440 EUR; conversion remains 40%; four comparisons pass before and after |
| Custody | 2170 protected fixture/evidence/pin files unchanged; private drafts unchanged at validation |

The isolated checkout is a fresh local clone with the reviewed working-tree
changes overlaid. Dependency downloads may use uv's cache; this was a clean
environment, not a claim of an empty-cache installation. The offline test used
the inherited Python socket audit guard, including dbt subprocesses; it is not
an OS firewall.

## Three results

| Through Ossie | Conversion | Static preservation | Target execution | Target oracle agreement |
|---|---|---|---|---|
| MetricFlow → MetricFlow | PASS | MISMATCH | BLOCKED | BLOCKED |
| Power BI → Power BI | PASS | Bounded PASS | NOT_EXECUTED | BLOCKED |
| MetricFlow → Power BI | PASS | MISMATCH | NOT_EXECUTED | BLOCKED |

Returned MetricFlow has no time spine, entities or default time dimensions;
eleven query attempts stop at missing-spine initialization. The Power BI native
round trip passes all nine bounded checks. The cross-platform output substitutes
nine annotated `BLANK()` measures, including both headlines, and captures 20
warnings, including a skipped relationship. These observations reproduce the
existing sealed [MetricFlow attempt](../results/task04-showcase/to-metricflow/metricflow/execution.json),
[Power BI comparison](../results/task04-showcase/powerbi/core/report.json), and
[cross-platform warnings](../results/task04-showcase/to-powerbi/metricflow/warnings.json).

**No converted-target numerical agreement is established.** The EUR 440 result
belongs only to the executed edited source. No Power BI Desktop refresh or DAX
query was executed; TOM checks do not change that boundary.

## Exact fresh evidence and reproduction

Complete fresh runtime files are local and ignored, not a replacement for sealed
historical evidence:

- `.tmp/three-demo-validation/offline/report.md` and `report.json`: all three
  experiments, exact definitions, conversion logs, SQL, oracle comparisons and hashes.
- `.tmp/three-demo-validation/clean-checkout/.tmp/three-demo-final/report.json`:
  final implementation executed in the new environment.
- `.tmp/three-demo-validation/refund/`: before/after/returned execution records,
  edited source, Ossie YAML and returned manifest.
- `.tmp/three-demo-validation/full-check.log` and `task02-replay.log`: complete checks.

Use [QUICKSTART](QUICKSTART.md) to regenerate your own bundle. CI uploads complete
focused evidence as `three-experiment-evidence-<os>` when the new workflow runs.
This record does not claim that remote artifacts already exist.

## Validation findings and limits

The first hygiene run exposed that `.gitignore` is hash-bound too. Its exact
revision is now bound to the original export hash; original provenance is unchanged.
The first Task 02 repeatability check overlapped implementation edits and detected
changed source/test custody hashes. Its failed report and diff are retained locally.
The stable rerun passed without changing historical evidence or converter behavior.

Windows was executed. Linux and macOS commands were reviewed, and CI jobs are
configured for all three systems; **Linux/macOS execution remains unverified**
until those jobs run. No general platform or model-equivalence claim follows from
this one synthetic fixture. The full nine-route driver remains available with its
existing default behavior.

The two private publication files are ignored and untracked. The three older
editorial files (`ARTICLE_DRAFT.md`, `ARTICLE_OUTLINE.md`, and
`CONVERSION_ARTICLE_DRAFT.md`) were already present on remote `main` at `6bb0457`;
they remain unchanged. New publication advice is kept in ignored local storage.

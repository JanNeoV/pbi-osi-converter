# Power BI runtime validation — 2026-10-20; closeout 2026-10-21

**The original core model and its unmodified official Ossie return both executed
successfully in local Power BI Desktop and matched the independent DuckDB oracle.**
The separate selector candidate failed to load both before and after conversion.
The MetricFlow-generated Power BI model failed to open; its measures were not executed.
These findings supplement, rather than rewrite, the historical NOT_EXECUTED records.

Run from base `461b415b945cd6cf23e4025a788b96a015bc8454` on branch
`validation/powerbi-runtime`. No converter, dependency pin, fixture, historical
evidence, DAX or relationship definition was changed. No cloud workspace was
created or used to execute queries.

| Experiment / existing candidate | Conversion | Desktop model load | DAX execution | Numerical oracle agreement | Semantic / formatting preservation |
|---|---|---|---|---|---|
| 1: native core | N/A | PASS; refreshed | PASS | PASS, six values | Bounded numeric behavior verified; live DAX and static formats match input |
| 1: native selector baseline | N/A | FAIL: conflicting format properties | NOT_EXECUTED | UNVERIFIED | Dynamic formatting and selection behavior unverified |
| 2: core Power BI → Ossie → Power BI | PASS, both legs rerun | PASS; refreshed | PASS | PASS, six values; also equals native | Nine static checks pass; live DAX/static formats retained; visual rendering untested |
| 2: selector Power BI → Ossie → Power BI | PASS, both legs rerun | FAIL: same format-property conflict | NOT_EXECUTED | UNVERIFIED | Eleven static checks pass, including preservation of the invalid properties; runtime unverified |
| 3: existing MetricFlow → Ossie → Power BI | PASS in retained conversion evidence; not rerun here | FAIL: no eligible Service workspace | NOT_EXECUTED | UNVERIFIED | Static MISMATCH: nine annotated blanks and skipped relationship; runtime formatting unverified |

**Executed results.** Values below are actual DAX results for both core models,
not SQL values substituted for Power BI execution. Each model separately returned
12 Sessions rows and six Orders rows.

| Context | Native rate | Returned rate | Oracle rate | Native EUR | Returned EUR | Oracle EUR |
|---|---:|---:|---:|---:|---:|---:|
| Overall | 0.4 | 0.4 | 0.4 | 390 | 390 | 390 |
| Direct | 0.5 | 0.5 | 0.5 | 300 | 300 | 300 |
| Indirect | 0.25 | 0.25 | 0.25 | 90 | 90 | 90 |

The following identical queries ran through Desktop's bundled ADOMD.NET client
against each Desktop process's own local `msmdsrv.exe`, after Desktop refresh:

```dax
EVALUATE ROW("Sessions", COUNTROWS(Sessions), "Orders", COUNTROWS(Orders))

EVALUATE
ROW("Conversion Rate", [Conversion Rate], "Paid Revenue", [Paid Revenue])

EVALUATE
SUMMARIZECOLUMNS('Sales Channel'[sales_channel],
    "Conversion Rate", [Conversion Rate], "Paid Revenue", [Paid Revenue])
```

`$SYSTEM.DBSCHEMA_CATALOGS` and `$SYSTEM.TMSCHEMA_MEASURES` were also queried.
The latter confirms the submitted measure expressions and formats `0.0%` and
`#,0.00 "EUR"` in both live engines. This verifies format metadata, not rendered
cards. Desktop automatically raised runtime compatibility from submitted 1601
to 1606; the submitted BIM bytes remained unchanged. No manual model repair or
replacement DAX was used. Agreement is bounded to the queries above.

**Actual failures.** The first load attempt used the existing embedded-data
[selector baseline](powerbi-task05/baseline.model.bim). Desktop displayed:

> The Measure 'Sessions'[Selected KPI] has both FormatString property and FormatStringDefinition property defined which is not supported scenario.

The freshly generated selector return displayed the identical error. Neither original was repaired. Selection and formatting in these
as-generated candidates remain **NOT_EXECUTED**. The separate disposable repair
experiment below does not change their **FAIL** classification. The successful core candidates are separate existing models,
not selector candidates with the failing measure removed.

The unchanged [MetricFlow-generated BIM](../results/task04-showcase/to-powerbi/metricflow/output.model.bim)
displayed **“Unable to open document” / “No eligible workspace found from
PowerBI Service.”** Its Direct Lake partitions reference a generated OneLake URL
with zero GUID placeholders. That definition is consistent with Desktop's
workspace request; no further cloud investigation was attempted. This is an
observed opening failure, not an observed blank result or a DAX calculation error.
The nine annotated `BLANK()` substitutions include `[conversion_rate]` and
`[paid_revenue]`; all remain **unexecuted**. The existing
[20 warnings](../results/task04-showcase/to-powerbi/metricflow/warnings.json),
including the skipped relationship, remain intact. Work on this route stopped
at the genuine environment/cloud boundary.

**Tools and retained evidence.** Windows Store Desktop **2.159.883.0** was
installed, launched and controlled successfully. Local Analysis Services and
bundled `Microsoft.PowerBI.AdomdClient.dll`: **17.0.93.23**; engine file version
`2025.0170.0093.23 ((releases/2026/09.4).260909-0937)`.
Python **3.12.10**, DuckDB **1.5.6**, PowerShell **5.1.26100.9711**.
Official `apache-ossie` and `apache-ossie-microsoft` **0.2.0.dev0**, verified
against pin `6ebc5e5612467dac405985f44ea078b39c891bee`.
Import warned that shared M expressions are preserved in the Power BI stash;
both exports emitted no warnings.

Ignored local captures are under `.tmp/powerbi-runtime-20261020/`:

- `native-core-execution.json` and `roundtrip-core-execution.json`: raw query
  text, returned rows, UTC timestamps, catalog identity and versions.
  Machine identifiers remain only in the local originals.
- `oracle.json`, `oracle/*.sql`, `oracle/*.json`: freshly executed existing
  independent SQL over the unchanged raw CSV fixture, including channel values.
- `verification.json`: comparisons of actual DAX with oracle rows, live measure
  metadata, all nine/eleven static checks, and unchanged hashes for all five
  submitted BIMs. `model-manifest.json` identifies exact source/copy SHA-256s;
  `capture-sha256.json` inventories the retained captures.
- `*-load-*.png`, `*-error.txt`, `roundtrip-core-loaded-0.png`,
  and `*-msmdsrv.log`: real Desktop dialogs, successful refresh capture and
  local engine logs. Screenshots are kept locally, not published.
- `core-to-ossie/`, `core-from-ossie/`, `to-ossie/`, `from-ossie/`: exact
  conversions, warnings, logs and pinned provenance. `query.ps1`, `verify.py`
  and the project folders retain the local execution setup.

The first helper invocation used the nonexistent subcommand `leg`; it failed
before conversion and was corrected to the existing `convert` command.
PowerShell initially refused the unsigned local query script; it was then
executed with process-local `-ExecutionPolicy Bypass`, without changing the
machine policy. Neither setup error was a model or DAX failure.

**Exact reproduction, using fresh paths.**

1. Use the frozen environment from [QUICKSTART](QUICKSTART.md), at the repository
   root. The historical core BIM contains a machine-local `FixtureRoot` binding.
   For exact-byte reproduction, use the location recorded in that existing BIM.
   On another machine, change **only a disposable native copy's** `FixtureRoot`
   to the same canonical CSV fixture, record the binding diff/hash, then convert
   that copy. Keep its returned model unmodified. This is a new environment-bound
   reproduction, not the exact submitted bytes of the retained run.
2. In Desktop, choose **Blank report**, then **Save As → Power BI project files
   (*.pbip)**, saving `.tmp/runtime-repeat/Shell.pbip`. Close that report.
   This run successfully saved a shell without importing data first.
3. Rerun the official conversions from the repository root:

   ```powershell
   $py = '.tmp/semantic-lab-venv/Scripts/python.exe'
   $work = '.tmp/runtime-repeat'
   $core = 'labs/semantic_interop/results/task04-showcase/powerbi/core/source.model.bim'
   $selector = 'labs/semantic_interop/showcase/powerbi-task05/baseline.model.bim'
   & $py -m semantic_lab.showcase_powerbi convert --source $core --output "$work/core-to-ossie" --direction to-ossie
   & $py -m semantic_lab.showcase_powerbi convert --source "$work/core-to-ossie/output.ossie.yml" --output "$work/core-from-ossie" --direction from-ossie
   & $py -m semantic_lab.showcase_powerbi convert --source $selector --output "$work/selector-to-ossie" --direction to-ossie
   & $py -m semantic_lab.showcase_powerbi convert --source "$work/selector-to-ossie/output.ossie.yml" --output "$work/selector-from-ossie" --direction from-ossie
   ```

4. Create five sibling directories beneath `$work`: `native-core`,
   `roundtrip-core`, `native-selector`, `roundtrip-selector`, `metricflow`.
   In each, copy the shell's `Shell.pbip` and `Shell.Report/` (excluding `.pbi`
   and `.platform`). Create `Shell.SemanticModel/` containing **only** the shell's
   `definition.pbism` and a byte-for-byte copy of the corresponding model below,
   named `model.bim`. Do not copy a cache, TMDL `definition/`, or pending changes.

   | Project | File to copy as `Shell.SemanticModel/model.bim` |
   |---|---|
   | native-core | `$core` |
   | roundtrip-core | `$work/core-from-ossie/output.model.bim` |
   | native-selector | `$selector` |
   | roundtrip-selector | `$work/selector-from-ossie/output.model.bim` |
   | metricflow | `labs/semantic_interop/results/task04-showcase/to-powerbi/metricflow/output.model.bim` |

   The Desktop-created shell used PBIP 1.0, PBIR 4.0 and PBISM 4.2, with
   `datasetReference.byPath.path = "../Shell.SemanticModel"`.
   [Microsoft documents TMSL support in PBISM 4.0+](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-dataset#definitionpbism).
   Preserve those shell files; no hand-authored report schema is necessary.
5. Open each project's `Shell.pbip` separately. For the two core models, choose
   **Home → Refresh**, wait for completion, then run each `EVALUATE` above in
   **DAX Query View** and retain results. The captured automated run instead used
   the equivalent ADOMD connection to each process's local port; it did not
   deploy a second database. Record the first error for failed openings.
   If MetricFlow opens in another environment, its exact headline query is
   `EVALUATE ROW("Conversion Rate", [conversion_rate], "Paid Revenue", [paid_revenue])`;
   distinguish an actual blank from a query that never ran.
6. Re-execute the maintained oracle and retain its SQL/results:

   ```powershell
   & $py -c "from pathlib import Path; from semantic_lab.showcase_powerbi import _oracle_evidence; from semantic_lab.integrations import write_json; p=Path('.tmp/runtime-repeat'); write_json(p/'oracle.json', _oracle_evidence(p))"
   ```

   Compare the overall/channel records with the actual DAX outputs. For a selector
   that opens on another version, use the unchanged
   [validate.dax](powerbi-task05/validate.dax) and [B2 visual checks](TUTORIAL.md#b2-add-and-test-the-dynamic-selector).
   Do not turn a repaired candidate into evidence for the as-generated route.

The 2026-10-20 run used Desktop load/refresh, live DAX/DMV queries, oracle
comparisons and preservation checks. Closeout verification is recorded below.
No application or converter code changed; private editorial material is excluded.


## Public evidence and independent verification

The [public package](runtime-evidence-20261020/provenance.json) contains allowlisted
extracts of the actual [native](runtime-evidence-20261020/native-core.json) and
[returned](runtime-evidence-20261020/roundtrip-core.json) query captures. Query
strings, column names and numerical rows are unchanged. It also contains the four
executed oracle SQL files and typed results, model hashes, converter provenance,
live DAX/format metadata and [comparisons](runtime-evidence-20261020/comparison.json).
Raw capture SHA-256s link each extract to the unchanged local originals.
Catalog identities, absolute machine paths, ports, PIDs and private DMV fields
are omitted; raw screenshots and logs remain local. **159/159** files matched the
retained capture inventory at closeout. Public extracts have their own
[SHA-256 inventory](runtime-evidence-20261020/sha256.json).

Run the small evidence verifier in the frozen lab environment:

```powershell
& .tmp/semantic-lab-venv/Scripts/python.exe labs/semantic_interop/showcase/runtime-evidence-20261020/verify.py
```

It checks published hashes and captured numerical rows against freshly executed
independent DuckDB SQL over the unchanged CSV fixture. This is evidence checking,
not a fresh Desktop execution. Actual Desktop reproduction uses the steps above;
the portable [ADOMD capture script](runtime-evidence-20261020/query.ps1) accepts a
local Desktop port, a query JSON file and a fresh output filename. A port is a
local runtime input, never a published fixed value.

## Tabular Editor assessment — 2026-10-21

**Unavailable in the inspected environment; not introduced as a dependency.**
`Get-Command te,TabularEditor.exe` found neither command. Searches of standard
Program Files/user Programs installations, user-local executable/WinGet links,
Downloads and Windows uninstall registrations found no Tabular Editor installation.
The actual `te --help` probe returned command-not-found. Consequently no installed
version or supported flags could be verified, and conflict detection by Tabular
Editor is **NOT_EXECUTED**, not a successful or failed validation test. No account,
cloud workspace or paid resource was created. This scoped search does not prove
that no portable copy exists elsewhere on the machine.

The three products must be distinguished:

- **TE2 `TabularEditor.exe`**: Windows command-line automation, with scripts and
  model build/deployment options. Its syntax is distinct from `te`.
- **`te`**: separate cross-platform limited preview, expiring **2026-10-31**;
  evaluation requires a Tabular Editor account. Help output is authoritative.
- **TE3**: desktop application, not the `te` executable.

These distinctions and preview limits are from the official
[TE2 documentation](https://docs.tabulareditor.com/en/features/Command-line-Options.html)
and [CLI command reference](https://docs.tabulareditor.com/en/features/te-cli/te-cli-commands.html).

| Requested capability | Documented `te` feature, not locally executed | Actual local alternative |
|---|---|---|
| BIM/TMSL inspection | `get` / `list`, model file input | JSON inspection of copied BIMs |
| Validation / conflicting format properties | `validate`; detection of this exact conflict unverified | Original Desktop rejection; explicit two-property inspection |
| Native vs returned diff | `diff` | Existing normalized comparisons and exact repair patches |
| Local Desktop connection | `connect` / local-instance support | Bundled ADOMD.NET to the selected Desktop child engine |
| DAX execution | `query` | Actual captured DAX rows through ADOMD.NET |
| Machine-readable tests | JSON findings, test results | JSON query captures and independent verifier |

The official [JSON output reference](https://docs.tabulareditor.com/en/features/te-cli/te-cli-findings.html)
distinguishes findings from successful query result rows. Those documented
capabilities could simplify future inspection and assertions, but no local
benefit or conflict detection was measured here. The already working bundled
ADOMD.NET path meets this experiment's needs without preview expiration or a
new account. Do not interpret the feature list as tested command recipes.

## Minimal selector repair — REPAIRED / EXPERIMENTAL

Original native/returned selector load failures remain **FAIL**. On four separate
disposable copies (native/returned baseline and native/returned changed-default),
remove only `Sessions[Selected KPI].formatString`, whose value is `0.0%`.
Retain `formatStringDefinition` and every other model property. The
[repair manifest](runtime-evidence-20261020/repair-manifest.json) records exact JSON
pointer patches and before/after hashes. Reinsert the removed property and the
parsed original model is exactly restored. JSON whitespace reserialization is
not a business-definition change. No DAX, relationships, input data, partition M
or selector semantics were edited. Changed-default DAX was already present in its
historical candidate; its returned copy came from the same pinned official
converter before applying the one-property repair.

The baseline native and returned repairs loaded, refreshed and executed. For each
model, all 12 combinations of selection (none, rate, revenue, multiple) and channel
(overall, Direct, Indirect) matched independent oracle values and the intended
selector contract. Headline rate/revenue were also queried in every context:

| Selection | Overall Selected KPI | Direct | Indirect |
|---|---:|---:|---:|
| None / Conversion Rate / multiple | 0.4 | 0.5 | 0.25 |
| Paid Revenue | 390 | 300 | 90 |

The changed-default returned repair also loaded, refreshed and executed: no/multiple
selection returned **390 / 300 / 90**, explicit rate still **0.4 / 0.5 / 0.25**,
and explicit revenue **390 / 300 / 90**. The native changed-default attempt and
its outcome differ: it loaded but refresh failed with **Sessions: A cyclic
reference was encountered during evaluation**, blocking three queries. Its DAX
acceptance is **NOT_EXECUTED**. No further model change was attempted. A dialog
dismissal retry was blocked by Windows window targeting, so UI input stopped;
the local screenshot/error capture is retained. The same unchanged partition
definitions refreshed in the returned copy; the cause of this one failure was
not established. Do not claim both changed-default candidates passed.

Dynamic formatting was tested separately: live DMV metadata retained the dynamic
expression, and evaluating that expression as DAX in each selection/channel
context returned `0.0%` or `#,0.00 "EUR"` as intended. This verifies expression
results and metadata, **not actual card rendering**. Rendered cards and interactive
slicer behavior remain **NOT_EXECUTED**. Numeric filters were applied explicitly
with `TREATAS`; query results do not inherit report slicers.

[Experimental captures](runtime-evidence-20261020/selector-experimental.json)
retain query text, raw numerical rows and separate format-expression results.
One early returned-baseline probe ran before refresh completed and returned
empty-model blanks; that attempt is retained locally and excluded from acceptance.
The accepted capture followed completed refresh and verified 12/6 row counts.
No failed or premature attempt is evidence for the as-generated selector path.
Stop at this minimal repair; it is not a general selector repair workflow.

To repeat, copy the existing baseline and changed-default BIMs to fresh work
directories. Convert each original through the pinned `convert` commands above
**before** repairing. For each of the four inputs, use a separate destination:

```python
import copy, json
from pathlib import Path

source = Path("path/to/original.model.bim")
destination = Path("path/to/disposable/repaired.model.bim")
assert source.resolve() != destination.resolve() and not destination.exists()
original = json.loads(source.read_text(encoding="utf-8-sig"))
repaired = copy.deepcopy(original)
measure = next(m for t in repaired["model"]["tables"]
               if t["name"] == "Sessions" for m in t["measures"]
               if m["name"] == "Selected KPI")
assert measure["formatString"] == "0.0%" and "formatStringDefinition" in measure
del measure["formatString"]
destination.write_text(json.dumps(repaired, indent=2), encoding="utf-8")
```

Package each copy in its own fresh PBIP shell as above, open, refresh and check
row counts before accepting results. The experimental JSON holds the exact DAX
queries for each successful variant. Extract one variant for the capture helper:

```powershell
$evidence = Get-Content labs/semantic_interop/showcase/runtime-evidence-20261020/selector-experimental.json -Raw | ConvertFrom-Json
$evidence.executions.'native-selector' | ConvertTo-Json -Depth 30 | Set-Content .tmp/selector-queries.json
# Set $localPort to the intended Desktop instance's current local engine port.
powershell -NoProfile -ExecutionPolicy Bypass -File labs/semantic_interop/showcase/runtime-evidence-20261020/query.ps1 -Port $localPort -QueryJson .tmp/selector-queries.json -OutputFile .tmp/selector-repeat.json
```

Use the returned changed-default query set for either changed-default copy so the
separate format-expression query uses its already-authored revenue default.
These are local read-only DAX captures; they neither deploy nor modify a model.

## Closeout verification

- Public package verifier: PASS for 12 core numerical values, 36 repaired selector
  contexts and 36 separate format-expression evaluations against fresh DuckDB SQL.
- Portable ADOMD helper: executed count, headline and channel queries against the
  refreshed repaired returned baseline; all three queries completed successfully.
- Public repository checker with `--after-demo`, local Markdown links, capture
  metadata scan and Git whitespace check (`cr-at-eol` for existing CRLF docs): PASS.
- Preservation: 2,577 pre-existing tracked files checked; only nine explicitly
  selected summary/revision documents changed. All historical evidence, fixtures,
  converter code and dependency pins remain byte-identical; 159 raw capture hashes
  still match. Private editorial files are ignored/untracked; the local article
  was backed up before editing.
- Focused existing regression suites and final-head CI status are reported in the
  accompanying PR. The known historical Ubuntu replay issue is outside this diff.

No preview CLI, cloud resource, new framework or converter repair is introduced.

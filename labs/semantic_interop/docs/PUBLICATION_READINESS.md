# Publication readiness review — 2026-10-19

**Verdict: READY WITH MINOR FIXES (bounded, evidence-led article).**

The proposed 600–800-word article is editorially scoped to Conversion Rate and
the Power BI `SWITCH` selector, and distinguishes conversion, static
preservation, actual runtime, and numerical equivalence. The article is **not
tracked in this repository**. It remains a separate private Markdown draft.

## Baseline and reproduction provenance

- Reviewed public default branch: `main` at
  `6f342299380d5ba6940d5429c98015de39e146dc` (19 October 2026).
- [Three-experiment CI run](https://github.com/JanNeoV/pbi-osi-converter/actions/runs/39160283961):
  **success**, Windows, macOS and Ubuntu, for that SHA. Focused CI runs the
  pinned setup, three-route runner and offline tests on each host. This is
  **previously recorded GitHub CI execution**, not a fresh run performed during
  this publication review.
- Previous [local acceptance record](../showcase/ACCEPTANCE.md): Windows Python
  3.12.10, 92 lab tests, 173 parent tests with one skip, 31 matching source
  oracle comparisons, five injected mistakes caught, and the documented
  refund exercise. Those are historical local measurements, not new results.
- The reviewer environment for this addendum has Python 3.13 and no functional
  outbound git/PyPI access. It cannot run a fresh Python 3.12 checkout,
  isolated install or official converters. Fresh-clone execution, fresh
  dependency provenance, fresh hash custody and a new entire-suite regression
  **have not been performed**. Never treat the prior CI result as a fresh
  Windows manual run.

## Five-row verification matrix

| Scope | Conversion / source result | Static definition check | Target execution | Target numerical agreement | Evidence type |
|---|---|---|---|---|---|
| MetricFlow → Ossie → MetricFlow | PASS; original 31/31 source comparisons | MISMATCH: time spine, entity declarations and aggregation defaults lost | BLOCKED: 11 attempted headline queries stop at initialization | BLOCKED | Sealed captures, prior focused CI |
| Power BI → Ossie → Power BI (core) | PASS | Bounded PASS: normalized DAX, relations, columns, partitions, M and formats retained | NOT_EXECUTED in Desktop | BLOCKED | Sealed captures, prior focused CI |
| MetricFlow → Ossie → Power BI | PASS | MISMATCH: nine annotated `BLANK()` measures incl. headlines, skipped relationship | NOT_EXECUTED in Desktop | BLOCKED | Sealed captures, prior focused CI |
| MetricFlow refund edit | Source revenue 390 → 440 EUR, Direct 300 → 300, Indirect 90 → 140, Conversion Rate 40% unchanged | Changed predicate retained in Ossie text; unchanged return remains blocked; Power BI still annotated `BLANK()` | Source measured in DuckDB; returned target BLOCKED; Power BI NOT_EXECUTED | Source oracle PASS; converted target BLOCKED | Previously recorded Task 05, tutorial |
| Power BI selector default edit | Authored `SELECTEDVALUE` alternate changed from Conversion Rate to Paid Revenue in numeric and dynamic-format expressions | Baseline and edited Power BI round trips both pass bounded normalized definition comparisons | NOT_EXECUTED in Desktop | BLOCKED | Previously recorded Task 05; supplied candidate files |

Direct evidence: [three-path results](../showcase/RESULTS.md),
[source oracle](../results/task04-showcase/native/report.json),
[returned MetricFlow attempts](../results/task04-showcase/to-metricflow/metricflow/execution.json),
[Power BI native structural report](../results/task04-showcase/powerbi/core/report.json),
[MetricFlow-to-Power BI warnings](../results/task04-showcase/to-powerbi/metricflow/warnings.json),
[MetricFlow edit before](../results/task05-minimal/metricflow/baseline/source/execution.json),
[after](../results/task05-minimal/metricflow/include-refunded/source/execution.json),
and [selector before/after round-trip report](../results/task05-minimal/powerbi/report.json).

## Five tutorial usability findings

1. **Selector discoverability:** The default quickstart covered the Power BI
   **core** model, not its optional disconnected selector. The quickstart now
   points readers directly to the separate `SWITCH` exercise, explicitly
   distinguishing it from the three core paths.
2. **POSIX selector reproduction:** Tutorial B3 originally offered only
   PowerShell. It now includes macOS/Linux shell commands for both supplied
   candidate model versions with no Desktop requirements, retaining stdout,
   stderr, and static selector assertions. This added shell snippet has **not
   been independently executed** in this review.
3. **Acceptance versus semantic equivalence:** The quickstart explicitly
   notes that exit 0 means successful evidence checks and may include
   expected BLOCKED or NOT_EXECUTED targets; it does **not** demonstrate
   lossless conversion. This boundary remains highlighted.
4. **Platform coverage:** Historical acceptance text was stale after the
   three-OS GitHub workflow passed. The quickstart now links verified CI; the
   acceptance record has a dated addendum rather than retroactively changing
   its original platform claims.
5. **Desktop reproducibility:** The supplied TMSL models can be converted
   offline, but there is no verified ready-to-open PBIP/PBIX and no automatic
   real DAX runtime. Tutorial B describes manual setup, all/no/multiple KPI
   selections, channel filtering, and format checks; these remain expected
   cases, **not measured Desktop results**.

## Legacy Ubuntu CI diagnosis

[Older full lab workflow](https://github.com/JanNeoV/pbi-osi-converter/actions/runs/39160284146)
failed at `Verify Task 02 offline and deterministic` on Ubuntu; its Windows job
passed. GitHub job logs report `No module named semantic_lab` in the current
diagnostic worker. `scripts/check_task02.py` passes
`str(args.upstream_python.resolve())` into the worker. Linux `venv` executables
may be symlinks to base Python; resolving before execution can bypass the
target virtual environment and its editable installation. The diagnosis is
plausible and directly matches the captured error, but the repair has **not**
been applied or verified. This old lane should not be conflated with the
successful three-OS focused demo.

## Link, custody, and review boundaries

The GitHub connector was used to read the named repository files on `main`
and the edited documentation on the dedicated review branch. The review
confirmed the central evidence and TMSL specimen paths. It did **not** perform
a complete site-wide HTTP link crawl or rehash all 2,170 protected files.
Only review documents were changed on this branch; no canonical fixture,
converter, dependency-pin or sealed-evidence path was intentionally edited.
A complete local `git diff` custody check is still required before merge.

The earlier article,
[Maintaining Power BI Semantic Models as Snowflake Semantic Views](https://medium.com/@jan.busse/maintaining-power-bi-semantic-models-as-snowflake-semantic-views-36cb4bcd9d56),
uses a targeted two-platform translation with explicit decisions on supported
semantics. This article examines a common interchange format and why adapter
round trips do not imply cross-platform numerical equivalence. The claims
are complementary; no general correctness claim for the older converter is
inferred here.

## Before publishing or merging

- Execute the new POSIX selector commands or have CI validate them. For
  Windows, verify the documented PowerShell version in a fresh checkout if
  newly claiming such a run.
- If a Desktop session is available, record actual source and returned model
  loading, query values, selector defaults, filters, and card formats. If not,
  retain the `NOT_EXECUTED` disclosure in the article.
- Execute regression, public repository/link, whitespace and hash-custody
  checks in a Python 3.12 checkout, then resolve any findings. A clean CI
  result for the existing focused workflow does not retroactively certify
  these new documentation changes.
- The article is private. Do not publish or merge automatically.

**Editorial conclusion:** The bounded article is defensible based on
existing reproducible CI and historical evidence. The outstanding actions are
verification of new tutorial commands and a final repository hygiene pass,
not a requirement for new semantic-model research.

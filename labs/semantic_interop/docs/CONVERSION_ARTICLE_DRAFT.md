# Two metrics, three platforms: what an official conversion actually proves

*Research article update, October 18, 2026. Local draft, unpublished.*

Direct converts at 50%; Indirect converts at 25%. The company's conversion rate
is 40%, because four of ten eligible sessions convert. Averaging the percentages
would produce 37.5%. A familiar metric name does not tell us which calculation a
converted model will execute.

Our reproducible example uses twelve sessions and six orders. Its two headline
metrics are **Conversion Rate** and **Paid Revenue**: Direct returns 50% and EUR
300, Indirect 25% and EUR 90, and overall 40% and EUR 390. Supporting counts, AOV
and edge slices remain internal validation. The
[five-minute demonstration](../showcase/README.md) contains the runnable business
example; the [nine-path results](../showcase/RESULTS.md) separate conversion,
static preservation and numerical execution.

A January 6 session produces a paid EUR 80 order on February 1. We attribute it to
the January session cohort and observe orders strictly before March 1. January
and February cohort revenues are EUR 270 and EUR 120; transaction-month revenues
are EUR 190 and EUR 200. Both totals equal EUR 390. Keeping the grand total while
changing the date role changes the business question.

The native official MetricFlow engine generates the queries; DuckDB executes them
and separate raw-table reference SQL. All eleven headline cases match, within a
31-case internal regression that also detects five deliberately injected SQL
faults ([native evidence](../results/task04-showcase/native/report.json)).
These are bounded observations on synthetic data, not a proof of general
semantic equivalence. The source's explicit derived expressions retain eligible
cohorts with no paid activity; this is authored modeling, not converter repair.

The unchanged official MetricFlow → Ossie → MetricFlow experiment completes both
conversions and produces parseable files. Dataset names and predicate text survive,
but entities, default aggregation time and the time spine disappear. Dependency
structure is flattened and reconstructed. None of the eleven headline queries
executes after the round trip: engine initialization requires the missing spine.
There is no target number to compare, and no measured numerical mismatch to claim.
The [raw artifacts and diagnostics](../results/task04/metricflow/headline-summary.json)
retain the original definitions, actual generated files and empty converter issue
lists. Cohort/cutoff SQL remains an external source-model assumption.

Power BI adds a different boundary. We start with an authored model and test its
own official Power BI → Ossie → Power BI path, independently of the MetricFlow
export. Case A carries the two metrics and hidden count dependencies. Case B adds
one disconnected two-choice selector and one `SWITCH` measure. Inspected DAX,
relationships, filter settings and format metadata survive through DAX expressions
and `POWER_BI` extensions. That preservation is useful for a Power BI round trip;
it does not make DAX portable SQL. Single, absent and multiple selection states,
channel filters and percent/currency rendering still need the Power BI engine.
The [supplied queries and evidence](../fixtures/conversion_demo/targets/powerbi/README.md#task-04-official-round-trip)
record DAX execution as `NOT_EXECUTED`.

We also built the current official Java Databricks adapter separately, passed its
100 upstream tests, and exported and re-imported the actual Ossie output. The
YAML parses, but twelve notices record metadata/field losses, and unresolved
filter identifiers remain ([captured report](../results/task04/databricks/report.json)).
No Databricks engine has analyzed or executed it.

The next independent experiment belongs to Charlotte. The
[Databricks handoff](../showcase/DATABRICKS_HANDOFF.md) pins the official converter, lists exact
inputs and commands, and separates generated Metric Views from the existing
manually authored candidate. It explains why a sessions-preserving SQL source and
native one-to-many joins impose different requirements. Databricks numerical
execution and oracle comparison remain `NOT_EXECUTED` until actual runtime results
are captured. A parsed YAML file cannot establish either result.

We checked current official documentation against upstream commit
`53418ba8a4b8a20951334fdb9d9638aa34b514e3` while retaining the baseline Python
converter commit `6ebc5e5612467dac405985f44ea078b39c891bee`. Identical development
version strings do not identify identical source; separate Git tree checks show
that the core Python, dbt and Microsoft directories are identical at these SHAs.
Each lane retains its own provenance; the [comparison table](../showcase/RESULTS.md)
separates completed conversion, structural validation, semantic observations,
numerical execution and oracle agreement.

The historical Task 02 result is separate: eleven matching cases required a local
key repair plus explicitly reconstructed temporal configuration, while three
filtered-revenue cases remained blocked. It is not evidence of an unchanged
lossless official round trip. Its [sealed evidence](../results/task02/verified-003/report.json)
and [research diagnosis](ROUND_TRIP_DIAGNOSIS.md) remain unchanged. The small handoff
extends that work with inspectable claims and a concrete next experiment.

Task 05 then executed one source-policy edit on the same sessions/orders rows:
including refunded orders changes Paid Revenue from 390 to 440 EUR while
Conversion Rate stays 40% ([before](../results/task05-minimal/metricflow/baseline/source/execution.json),
[after](../results/task05-minimal/metricflow/include-refunded/source/execution.json)).
The modified predicate survives as text in Ossie; the unchanged MetricFlow return
still cannot initialize, and the Power BI export substitutes `BLANK()` for the
unsupported headline expression. The [actual artifacts and warnings](../results/task05-minimal/metricflow/include-refunded/)
support that narrower conclusion. Native Power BI and Databricks round trips
preserve inspected definitions, including vendor extensions; neither engine has
measured those results. No numerically verified unchanged conversion happy path
has been demonstrated.

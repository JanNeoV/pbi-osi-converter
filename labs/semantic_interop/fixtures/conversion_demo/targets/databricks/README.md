# Databricks Metric View candidate

**[metric_view.yaml](metric_view.yaml) is manually authored. It is not output from
the official Apache Ossie converter. Databricks creation, target SQL analysis and
runtime comparison are `NOT_EXECUTED`.** A successful local YAML parse is only a
text/shape check. It is neither Databricks engine validation nor interoperability
evidence. Any actual converter-generated YAML must be retained separately unchanged.

The candidate exposes Sales Channel, Session Month and the same six fundamental
metrics as the canonical fixture. It deliberately omits Power BI's disconnected
selector and dynamic format logic. A Databricks dashboard may select which metric
to display without implying that DAX presentation semantics were converted.

The SQL source starts with sessions and uses `LEFT JOIN` for orders, retaining
non-ordering sessions in the denominator. Distinct session counts prevent the
optional extra order from inflating conversion. Orders after the fixed observation
cutoff are excluded in the join condition, so a session with only later orders
also remains in the denominator. Session dates start on **2026-01-01 inclusive** and precede **2026-03-01 exclusive**,
and the global eligibility filter applies to every metric. Counts remain zero on
empty input; sums remain NULL; ratios use `NULLIF` to preserve undefined results.

Orders belong to one originating session and its one channel. Session Month uses
`session_date`, so Paid Revenue is **session-cohort revenue**, January 270 EUR and
February 120 EUR as observed before cutoff. Transaction-date revenue, January
190 EUR and February 200 EUR, answers a different question. The dataset contains
observed whole-order statuses; it does not reconstruct status changes over time.

## Next contribution: genuine runtime comparison

Use the same two CSV fixtures with explicit types: session/order IDs `BIGINT`, dates
`DATE`, channel/status/reason `STRING`, eligibility `BOOLEAN`, amount
`DECIMAL(18,2)`. Replace `demo.semantic_interop` in the candidate and target queries
with an authorized Unity Catalog catalog/schema. This repository does not create a
cloud account, upload data or deploy a view.

In an authorized future experiment, load those exact rows, create the Metric View
from the candidate or separately retained actual converter output, and execute
[validate.sql](validate.sql). Compare its real results with the independent DuckDB
oracle at overall, channel, month and channel-month grains, including empty and
zero-conversion slices. Repeat with the optional extra paid order: expect 40%
conversion, five paid orders, 420 EUR revenue, and 84 EUR AOV. Record input hashes,
runtime/warehouse version, definition provenance and actual SQL/result artifacts in
the shared comparison format. A candidate pass must not be reported as a converter
pass; those are different experiment lanes.

## Version and official adapter boundary

Official documentation checked 2026-10-17 supports
[SQL-query sources, filtered aggregate measures and NULL-preserving aggregate ratios](https://docs.databricks.com/aws/en/uc-semantics/metric-views/yaml-reference).
The candidate uses YAML specification **1.1**, with comments; the documented minimum
for its metadata features is **Databricks Runtime 17.3**, or an appropriately current
SQL warehouse. YAML specification and runtime versions are separate. Native
`cardinality: one_to_many` joins require **18.1+**; this candidate instead uses a
sessions-preserving SQL source query. See the
[official feature matrix](https://docs.databricks.com/aws/en/uc-semantics/metric-views/feature-availability).
Actual target parsing remains unverified.

The frozen official adapter is
`.tmp/task02-replay/current/converters/databricks/java`. Its README requires
**Java 21+ and Maven 3.6+**. It supports the offline command
`java -jar PATH_TO_OFFICIAL_JAR export INPUT_OSSIE_YAML -o NEW_OUTPUT_YAML --source sessions`.
The `--source` choice is meaningful: generated native one-to-many joins may require
a later runtime. Current tooling availability and actual invocation outcomes are
recorded by the separate Stage B conversion runner. No handwritten converter or
dependency installation is substituted for an unavailable official adapter.

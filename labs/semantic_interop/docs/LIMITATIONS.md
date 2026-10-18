# Limits of the evidence

- Results cover one small synthetic fixture and six authored metric definitions,
  queried in 14 ways. They establish neither general converter correctness nor
  enterprise production readiness.
- Schema/model-shape validation, bounded semantic preservation and numerical
  execution are separate fields. A final semantic mismatch can have no executed result.
- The native MetricFlow/DuckDB path executes. The unmodified round-trip path fails
  before SQL compilation because required time-spine configuration is absent. No
  external configuration is silently merged into its output.
- Static signatures deliberately compare a bounded contract. They are conservative,
  not a theorem prover: different expressions might be equivalent. In this fixture,
  missing entities/time defaults and reassigned filtered aggregations are concrete
  coverage losses. Their runtime effects are not asserted.
- There is no native Ossie query engine in this experiment. Returned MSI is compiled
  with MetricFlow; an Ossie document itself is not executed as if it were SQL.
- Microsoft converter output is TMSL metadata with placeholder Direct Lake source
  IDs. No Power BI refresh, DAX engine execution, report visual, TOM or Fabric
  deployment occurs. Generated DAX is not independently executed or approved.
- The TMDL fixture is manually authored, has no data-loading partitions, and is for
  static inspection. The repository DAX grammar is narrower than Power BI. Its
  `UNSUPPORTED` statuses describe inspection coverage, not Power BI feature availability.
- Only literal query filters are used. If the compiler returns bound parameters,
  this milestone refuses execution rather than substituting values itself.
- No cumulative/conversion metrics, security roles, calculation groups, currencies,
  timezones, slowly changing dimensions, live data or performance benchmarking.
- Injected SQL faults demonstrate detection, not bugs discovered in a converter.
  Invalid duplicate dimension keys are rejected before any correctness claim.
- Dependency download requires network; subsequent benchmark execution does not.
  The Python socket audit test is a useful check, not an operating-system network sandbox.
- Checked evidence is a Windows capture. Platform-specific dependency inventories
  can differ; repeated runs must agree within each locked environment. Linux CI is
  configured but was not executed locally or published.
- Ossie is a commit-pinned development snapshot. Upstream behavior can change;
  refresh evidence deliberately when changing the pin.

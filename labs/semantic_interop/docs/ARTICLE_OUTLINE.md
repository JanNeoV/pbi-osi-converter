# Your Semantic Models Compile. But Do They Mean the Same Thing? Testing Apache Ossie, MetricFlow and Power BI

Working evidence-led article; not yet a published claim.

1. **The missing claim in conversion demos.** The earlier 46-measure Power BI audit
   established captured omissions and static mistranslations, not numerical equivalence.
   Introduce the three separate questions: valid structure, preserved meaning, same result.
2. **A reproducible experiment an engineer can inspect.** Show the five synthetic
   business tables, exact metric meanings, independent SQL oracle and the architecture
   diagram. Explain the order/item grain, null category and zero-denominator cases.
3. **A measured success.** Native MetricFlow compiles and executes 14 cases that agree
   with the oracle. Revenue is 265.30, orders 8 and AOV 33.1625. Limit the claim to this
   pinned fixture and query matrix.
4. **Schema-valid interchange is not an executable semantic contract.** Both official
   Ossie conversion calls succeed without reported issues, yet the returned manifest
   loses required time-spine configuration and cannot initialize MetricFlow. Show the
   small reproducer and static entity/filter differences. Do not say the round-trip
   model compiled or produced incorrect numerical results: it did neither here.
5. **Honest Microsoft boundaries.** Four measures become annotated BLANK placeholders,
   two relationships are skipped, and two emitted expressions lack DAX execution evidence.
   Separate converter-generated TMSL from authored TMDL and from native Power BI support.
6. **Prove the harness can reject plausible wrong answers.** Joining order totals back
   to items yields 510.60 instead of 265.30; averaging daily ratios yields about 0.66667 instead
   of about 0.73615. These are deliberate controls, not attributed upstream failures.
7. **Enterprise and AI implications.** Preserve grain, filter behavior, unsupported
   coverage and provenance in exchange contracts. Governed metrics are a promising
   agent interface only when their executable semantics have evidence. Link the
   separate agent evaluation follow-up, without claiming an agent accuracy improvement.
8. **Limitations and next evidence.** Tiny synthetic scope, unreleased Ossie commit,
   no Power BI runtime, no production claims. Offer reproducible artifacts and focused
   upstream regression cases rather than a universal interoperability claim.

Suggested figures: architecture diagram; the three evidence dimensions; native versus
round-trip outcome matrix; a before/after join-fanout example. Use the machine-readable
report as the source for all counts. Never combine native, controlled, metadata and
unsupported rows into an aggregate accuracy percentage.

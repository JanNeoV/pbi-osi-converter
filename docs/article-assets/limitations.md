# Limitations

- This is a narrow deterministic POC, not a universal DAX converter or production migration platform.
- The semantic-trap Snowflake comparison is labelled synthetic test evidence, not a live Snowflake export.
- Only the accepted typed IR patterns are generated; ambiguous, inactive-relationship, and context-dependent semantics remain review items.
- Candidate equivalence uses a committed small fixture dataset and does not prove arbitrary production-data equivalence.
- Source descriptions help reviewers but do not establish formula correctness.
- The default demo is offline. Live Snowflake verification is optional, separate, credential-dependent, and not part of acceptance.
- No Power BI Service, Fabric, Snowflake, or other deployment is performed.
- The existing `PBI_RELATIONSHIP_DRIFT_FCT_RESULT_DISTANCE_ID` strict baseline remains unresolved by design.

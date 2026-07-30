# Evidence-backed key findings

- Technically valid conversion can still be semantically wrong: `fnd_15fe871fa1b58924` detects `/60` where seconds-to-hours requires `/3600`.
- Human review is required for business meaning: `fnd_406ee6d2ec577e9b` prevents a numeric identifier from being accepted as an additive fact.
- Better source documentation and modeling reduce review effort: the three `EXACT_MATCH` findings resolve deterministically, while four ambiguous measures require review.
- Documentation alone does not prove formula correctness: `fnd_53e19af120db604e` flags uncertain generated prose, while numerical evaluators independently expose two formula failures.
- Unsupported constructs must remain explicit: `fnd_08ef87d4c78aace4` retains inactive-relationship DAX as unsupported.
- One canonical contract can reduce later Power BI/Snowflake drift: the committed maintenance proof shows common IR equality, unchanged unrelated objects, validation, and hash-guarded rollback.

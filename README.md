# Power BI to Snowflake semantic assurance POC

This clean companion repository reproduces one captured Power BI-to-Snowflake
conversion audit and three offline demonstrations of a governed alternative.
It makes no universal claim about Snowflake conversion. In this captured model,
all seven reviewed relationship endpoints matched, while the initial metric
output was incomplete and contained material semantic errors.

The operating idea is simple: abstract model conversion is only the start.
Business meaning still needs deterministic comparison and human review. An
agent-led interface makes that governed maintenance loop usable without
letting the model author executable DAX or SQL.

## Five-minute quickstart

Use Python 3.10 or newer. No Snowflake, Power BI, OpenAI, or other cloud
credentials are required.

```text
python -m pip install -e ".[dev]"
python semantic_poc/run_pbi_trial_v2_audit.py --check --json
python semantic_poc/run_demo.py --clean --check
python semantic_poc/run_agent_guided_conversion_demo.py --clean --check
python semantic_poc/run_end_to_end_demo.py --clean --check
```

For a concise narrated sequence:

```text
python demo/run_public_demo.py
```

CI uses `python demo/run_public_demo.py --check`, which removes presentation
pauses and remains fully offline.

## What each command proves

1. The captured `pbi_trial` audit inventories 46 Power BI measures, 21 emitted
   Snowflake metrics, 25 omissions, four confirmed mistranslations, and four
   potentially incorrect translations. All seven reviewed relationship
   endpoints matched. Runtime equivalence is `NOT_AVAILABLE`.
2. The separate `B_SEMANTIC_TRAPS` fixture demonstrates an offline,
   deterministic workflow that blocks unsafe output pending review.
3. The agent-guided demonstration uses a clearly labeled scripted provider.
   The agent selects bounded tools and improves the review interaction;
   deterministic code remains the parser, compiler, and validator.
4. The end-to-end fixture records a human decision, keeps accepted review
   memory confirmation-required, regenerates two target candidates, and reports
   zero unexpected cross-target drift. It does not deploy anything.

Counts from the captured 46-measure audit must not be combined with counts from
the later synthetic fixtures.

## What converted well

![Power BI and Snowflake relationship topology comparison](docs/images/relationship-comparison.svg)

The captured Snowflake YAML preserves the seven endpoint pairs in the Power BI
TMDL, including the normalized country/event and age-group/division chains and
the result-to-split path. This is a topology claim, not complete model or
runtime equivalence.

For ongoing maintenance, the canonical source is
`models/semantic/triathlon_semantic.yml`. Power BI TMDL and Snowflake YAML are
inspected or generated representations, not independent sources of truth.
Unsupported or ambiguous semantics stop at `MANUAL_REVIEW_REQUIRED`.

The offline guided demo now pauses on a deterministic finding and resumes only
with an offered `answer_ref`. It explicitly blocks filter-state logic such as
`ISCROSSFILTERED`, visual-scope logic such as `ISINSCOPE`, inactive
`USERELATIONSHIP` behavior, and measures that depend transitively on those
constructs.
The checked `target/semantic_manifest.json` is a compiled, reproducible input
used by the proposal demonstrations; it is not a second authoring source.

## Agent governance

![Agent-led review with deterministic execution](docs/images/agent-review-flow.svg)

The agent is the conversational interface, not the semantic compiler. It can
explain evidence, route proposal-only tools, and pause for a structured human
choice. Deterministic code alone parses expressions, resolves dependencies,
binds evidence to hashes, and emits candidate definitions. A candidate remains
unapplied; approval, application, live validation, and deployment stay outside
the demonstrated workflow.

## Evidence and boundaries

- [Evidence and claim map](docs/EVIDENCE.md)
- [Limitations](docs/LIMITATIONS.md)
- [Live-demo script](docs/DEMO.md)
- [Agent-governance flow](docs/images/agent-review-flow.svg)
- [Direct relationship comparison](docs/article-assets/relationship-comparison.md)
- [Captured conversion funnel](docs/images/conversion-funnel.svg)
- [Security policy](SECURITY.md)
- [Public export provenance](PUBLIC_PROVENANCE.json)

The checked `.pbit` in this public repository is a deterministic sanitized
projection of the private source artifact: its account, warehouse, database,
schema, and `SecurityBindings` content were removed or replaced. The portable
TMDL fixture is the audit input. The captured Snowflake YAML is preserved
unchanged because the findings are hash-bound to that output.

## Safety

All public demonstrations are local and proposal-only. They do not contact
Snowflake, Power BI Service, OpenAI, or GitHub; do not approve or apply a
semantic change; do not modify the source Power BI definition; and do not
deploy. Review memory is governed, versioned guidance that always requires
fresh confirmation. It is not model training.

This repository is licensed under Apache-2.0. See [LICENSE](LICENSE) and the
[third-party dependency notices](THIRD_PARTY_NOTICES.md).

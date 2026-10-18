# Repository decision: semantic interoperability lab

Decision date: 2026-10-16. Extend **pbi-osi-converter** with the additive
`labs/semantic_interop` research track. No new repository, remote, or publication.

| Criterion | A: public converter | B: private semantic-contract-poc | C: standalone lab |
|---|---|---|---|
| Proven code reuse | Direct use of TMDL inventory, bounded DAX and relationship checks | Similar inspection and larger contract/import infrastructure | Requires dependency on A or copying its code |
| Architectural cohesion | Adds execution evidence to existing semantic assurance | Fits semantics but mixes a neutral benchmark into a domain-specific contract | Clean research focus, separate maintenance |
| Public reproducibility | Already public, Apache-2.0, offline fixtures and CI | Needs a reviewed public export | Possible after new packaging and publication review |
| Credential independence | Existing default is offline | Includes optional Fabric/Snowflake workflows that must be separated | Can be designed offline |
| Scope isolation | Separate lab package and workflow | Canonical triathlon source rules complicate independent authoring | Strongest isolation |
| Publication potential | Extends an existing, explicitly limited evidence story | Private implementation cannot be the public reproduction path | Clear story but duplicates project context |
| Maintenance burden | One additive package; shares inspection and public safeguards | Additional export and compatibility work | Additional repository, CI, release and dependency lifecycle |
| Duplication risk | Low: new numerical oracle and execution harness | Medium: overlaps the existing public companion | Highest: would recreate existing inspection infrastructure |

A is the smallest viable option. The new package depends on the parent through an
editable relative path in its lockfile; it does not copy the existing parser or
replace its canonical model. Its Python 3.12 environment lives under `.tmp`, keeping
the parent's Python 3.10-compatible package and dependency declarations unchanged.

## Audit findings and boundaries

- The public README and audit runner describe 46 captured Power BI measures,
  21 emitted metrics, 25 omissions, four confirmed and four potential mistranslations.
  They explicitly do not establish runtime equivalence. These counts stay separate
  from the synthetic lab.
- Existing reusable components include `extract_powerbi_inventory`,
  `analyze_dax_measure`, `analyze_import_relationships`, the typed semantic IR,
  conversion trap fixtures, and offline CI. The new lab calls the inventory and
  relationship checks directly; it does not broaden the supported DAX grammar.
- `PUBLIC_PROVENANCE.json` binds existing exported files and protected inputs to
  hashes. Existing README, package configuration, CI, captured outputs, canonical
  YAML, schemas, golden fixtures and provenance remain unchanged. The new lab has
  independent documentation and an additional CI workflow.
- The private POC has dbt manifests, TMDL, canonical signatures, proposal governance,
  optional cloud workflows and substantial test coverage. No private code or data
  was copied. The existing public export already contains the inspection needed here.
- Local folders named after Snowflake semantic views do not identify a useful new
  target: their remotes point to other projects, including the private POC and an
  unrelated Fabric project. They are excluded from implementation.
- The public AI agent evaluation lab already supplies development/test separation,
  deterministic scoring and configuration freezing. The new work includes only a
  follow-up integration specification.

No changes to existing domain contracts, private datasets, production credentials,
triathlon applications or repository visibility are part of this milestone.

See the [lab README](../labs/semantic_interop/README.md) and
[upstream audit](../labs/semantic_interop/docs/UPSTREAM_AUDIT.md).

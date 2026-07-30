# Versioned semantic review registry

This directory is the POC's accepted review memory. The YAML file under `accepted/` is the authoritative rule; this Markdown file is only a human-readable view.

The registry contains one fixture-bound rule: seconds labelled as hours must use an exact scaled-sum divisor of 3600. Retrieval is deterministic and suggestion-only. Every source-pattern, unit, aggregation, relationship, and fixture signature must match exactly, and a human must still confirm any future proposal.

The registry is not model training, does not override the canonical dbt contract, and cannot approve, apply, or deploy a change.

# Next research milestone

Task 02 established a configuration-loss chain, three reduced counterexamples, an executed partial repair,
and a concrete contribution candidate. Prioritize these next steps:

1. Review the field-name key patch with the name-resolution discussion and PR315 authors. Confirm consumer compatibility
   before any submission. The current deliverable is local and unpublished.
2. Combine a reviewed key repair with a pinned PR437 implementation in a separate experiment. Add a focused investigation
   of filter dimension resolution and owning-dataset selection; do not replace MetricFlow templates by ad hoc string edits.
3. Decide an explicit interchange contract for default aggregation time and time-spine configuration. Consider vendor
   extensions or a documented runtime configuration interface separately from core-spec changes.
4. Extend numerical probes to derived offsets, non-day grains, null filling and metadata-driven behavior. Their present
   inventory entries and upstream references are not numerical evidence.
5. Run the existing Windows/Linux workflow after human review. Local Windows verification does not establish Linux CI success.
6. If Power BI runtime access is separately authorized, load equivalent typed data and execute independently reviewed DAX
   queries with relationship/filter context checks. TOM serialization alone cannot provide this evidence.

No new application, agent-evaluation framework or hosted service is required for these steps.

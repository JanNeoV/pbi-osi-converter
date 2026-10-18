# Task 02 validation and evidence custody

The final machine-readable check record is [validation-final/record.json](../results/task02/validation-final/record.json).
The complete added/modified file inventory is [file-changes.json](../results/task02/file-changes.json).
The original branch and commit remain unchanged; all work is local and unpublished.

## Protected starting point

Before editing, the 106 untracked milestone files were archived to `.tmp/semantic-task02/initial-milestone.zip`.
[baseline.json](../results/task02/baseline.json) records that archive's hash, the Git state, installed packages,
verified starting outcomes and hashes of all 418 original files. The final audit checks the archive again.

Exactly five original files were intentionally edited: the lab workflow, README, report schema, CLI and adapter.
The other 413 retain their original bytes, including the historical evidence, captured inputs, dependency lock,
oracle, typed comparator and benchmark specification. No tracked repository file or `fitness_agent` file changed.
The baseline environment retains every recorded package version.

The unchanged `python -m semantic_lab benchmark --check` command was run again under the socket guard.
All result fields and all 36 artifact hashes equal historical evidence. Its input-hash section correctly changes:
50 of the original 52 input hashes remain identical, two implementation hashes change, and three new inputs appear.
Post-implementation reports are therefore not claimed to be byte-identical to the historical report.

## Experiments and patch lineage

[verified-001](../results/task02/verified-001/report.json) retains the first sealed evaluation.
[verified-002](../results/task02/verified-002/report.json) repeats the final implementation and formatted patch.
[verified-003](../results/task02/verified-003/report.json) adds the final test-import cleanup to the implementation provenance.
An intermediate comparison correctly rejected a test-file hash change during execution; its retained difference
record shows identical query-case results. The final pair runs against stable sources.
Each capture refuses destination overwrites and verifies artifact hashes before copying. Two fresh runs under
the inherited offline guard produce identical normalized evidence. The guard denies Python socket connection
and name-resolution audit events in workers and dbt subprocesses; it is not an operating-system firewall.

The original [patch](../results/task02/upstream-repair/field-name-keys.patch) and all failing/initial test logs remain intact.
The [version 2 patch](../results/task02/upstream-repair-v2/field-name-keys.patch) only formats the newly added test.
Production module hashes and query-case results agree across the two evaluations. The final patch passes all
179 dbt tests and five snapshots, lint on modified Python files, formatting of the new test, and Git whitespace checks.
Current upstream passes 176 tests; the evaluated PR437 source passes 185 tests, each with five snapshots.
The three focused key regressions fail before the repair and pass after it. The PR315 counterexample is retained.

Networked acquisition was tested separately through `setup_task02.py` in a fresh destination. Its original
acquisition commands and volatile local environment paths remain in `.tmp/task02-replay/acquisition.json`;
a portable copy is separate from the normalized numerical evidence. The final formatted patch also passes
`git apply --check` against a clean checkout of the frozen upstream commit.

## Execution boundaries

Local validation runs on Windows and Python 3.12. The Windows/Linux workflow is extended, but remote CI was
not dispatched. Linux validation is `NOT_EXECUTED`: no installed WSL distribution or Docker runtime was available.
Other upstream Python versions were not tested locally. Power BI/DAX runtime and optional TOM validation are
`NOT_EXECUTED`; authored Microsoft round-trip probes provide static converter evidence only.

Native execution remains 14/14. The unmodified pinned and current round trips remain blocked. The local converter
repair plus explicit Level 3 reconstruction executes 11/14 cases, all matching the oracle; three remain blocked.
The ten detected controls include six deliberate numerical mismatches. No blocked upstream query is presented
as an observed numerical mismatch. The publication package and patch are review artifacts, not submissions.

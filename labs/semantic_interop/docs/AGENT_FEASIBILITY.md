# Follow-up specification: governed metrics versus direct SQL

Research question: on the same synthetic data and questions, does access to governed
metric discovery/query tools improve an analytics agent's numerical and semantic
accuracy compared with access to the database schema and direct SQL execution?

No agent experiment is included in this milestone. The deterministic native metric
path is usable, but the interchange path is not numerically established. Mixing that
failure with agent reasoning would make results hard to interpret.

Reuse the public `ai-agent-evaluation-lab` lifecycle rather than creating an evaluator:
development questions, frozen configurations, unseen test/holdout questions and the
existing distinction between structural output validity and strict acceptance.
Do not import its private datasets or historical cloud responses.

Use two tools for the governed arm: metric discovery returning definition, grain,
filters and identifiers; metric query accepting identifiers, dimensions and bounded
filters. Return typed rows, exact query text and evidence IDs. The direct-SQL arm
receives identical data access and a schema inventory. Both arms use the same model,
question set, resource budget and deterministic oracle. Ground truth must not be
visible to either arm.

Score numeric rows, selected population, grain, null handling, provenance and correct
abstention separately. Include empty sets, zero denominators, missing relationships
and questions outside governed coverage. Track invalid SQL and unsupported metric
requests separately from wrong answers. Freeze prompt/tool versions before evaluating
unseen questions; no tuning on test feedback.

The eventual interface exports lab case IDs, typed oracle outputs and fixture hashes
to the existing evaluator. It must not duplicate its sealing, scoring or provider
framework. Model calls, cost budgets and publication are a separate authorized scope.

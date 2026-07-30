# Scripted terminal demo

Run:

```text
python demo/run_public_demo.py
```

The command is deterministic and offline. In an interactive terminal it pauses
between sections; `--check` removes pauses for CI.

## 4–6 minute presenter sequence

1. **Context (0:00–0:30):** point to the source `.pbit`, portable fixture, and
   captured Snowflake YAML. Ask whether the business meaning survived.
2. **Captured gap (0:30–1:30):** show 46 source measures, 21 emitted metrics,
   25 omissions, four confirmed mistranslations, and seven matched relationship
   endpoints.
3. **Concrete findings (1:30–2:15):** explain `Result Rows`, `Split Coverage
   Rate`, and the divisor-60 unit defect in business language.
4. **Offline assurance (2:15–3:15):** show the deterministic workflow stopping
   at `BLOCKED_PENDING_REVIEW`.
5. **Scripted clarification (3:15–4:30):** clearly label the transcript as
   scripted. Ask whether seconds divided by 60 means minutes or whether hours
   require 3,600. The accepted fixture chooses the governed hours rule.
6. **Governed result (4:30–5:30):** show the recorded decision,
   confirmation-required review-memory suggestion, 5/5 corrected fixture
   equivalence, regenerated target candidates, zero unexpected drift, unchanged
   sources, and no deployment.
7. **Close:** the agent improves the interaction; deterministic code remains the
   compiler and validator.

Use this exact local command for the recording. Keep raw recordings, captions,
and project files outside the public repository. For presentations, keep a
pre-recorded fallback and never depend on a live service.


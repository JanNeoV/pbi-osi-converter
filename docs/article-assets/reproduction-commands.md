# Reproduction commands

Windows PowerShell:

```powershell
python -m pip install -e ".[dev,dbt]"
python semantic_poc/run_poc.py
python semantic_poc/run_demo.py --clean --check
python semantic_poc/run_end_to_end_demo.py --clean --check
semantic-agent demo --fixture semantic-trap --output-dir .\demo-output-cli --check
semantic-agent demo-finalize --demo-run .\demo-output --decisions .\semantic_poc\demo\review-decisions.accepted.yml --output-dir .\demo-finalized
python -m pytest semantic_poc/tests -q -p no:cacheprovider
dbt --no-version-check parse
python semantic_poc/run_quality_checks.py
python semantic_poc/run_poc.py --strict
python semantic_poc/run_conversion_benchmark.py --check
git diff --check
```

Bash uses the same commands with `/` path separators and no PowerShell continuation character.

`POC_DEMO_ACCEPTED` means the evidence is reproducible. It does not approve the blocked source conversion or perform deployment.

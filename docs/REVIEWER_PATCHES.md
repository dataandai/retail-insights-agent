# Reviewer Findings Fixed in This Artifact

This file is intentionally short and tied to concrete code changes. The authoritative proof is the code and tests in this ZIP.

## Fixed items

1. **Wildcard PII projection bypass**
   - `src/security/sql_guardrails.py` rejects `SELECT *` and `SELECT alias.*` while allowing `COUNT(*)`.
   - Tests: `tests/unit/test_sql_pii_projection.py`.

2. **Silent graph-to-imperative fallback**
   - `RetailInsightsAgent.answer()` no longer catches arbitrary LangGraph runtime failures and silently swaps to the imperative implementation. Real graph errors are logged and returned as controlled failures; `LocalCompiledGraph` is used only when `langgraph` is absent.
   - File: `src/agent/graph.py`.

3. **Mock eval metric weakness**
   - `evaluation/run_evals.py` now compares result sets row-by-row, preserving categorical/date/string columns and numeric tolerance. Swapped Texas/California values now fail.
   - Tests: `tests/unit/test_eval_result_comparison.py`.

4. **Bare `--user-id` trust boundary**
   - CLI requires a local token from `config/users.yaml` or environment variables. Production must replace this with SSO/IAM.
   - Files: `src/security/auth.py`, `src/cli.py`, `config/users.yaml`.

5. **Router false positives and delete synonyms**
   - Report-email requests route to an explicit unsupported action instead of branch/PII analysis.
   - `remove` / `erase` / `purge` report requests route to delete confirmation.
   - File: `src/agent/nodes/router.py`.

6. **LIKE wildcard broadening in delete scope**
   - `%`, `_`, and backslash are escaped in report-scope keyword/tag searches.
   - File: `src/database/reports_store.py`.

## Verification run

```text
python scripts/verify_runtime.py
Runtime verification OK: real LangGraph + SqliteSaver + BigQuery deps are importable and pinned.

USE_STUB_LLM=true USE_MOCK_BQ=true ALLOW_IN_MEMORY_CHECKPOINTER_FOR_TESTS=true python -m pytest -q
42 passed

USE_STUB_LLM=true USE_MOCK_BQ=true ALLOW_IN_MEMORY_CHECKPOINTER_FOR_TESTS=true python evaluation/run_evals.py --mode mock
Mode: mock
Pass rate: 10/10 = 100%
```

Live BigQuery + Gemini execution accuracy is still credential-dependent and must be run by the repository owner with:

```bash
python evaluation/run_evals.py --mode live --refresh-cache
python evaluation/run_evals.py --mode live
```

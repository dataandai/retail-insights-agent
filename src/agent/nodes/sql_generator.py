from __future__ import annotations

import re

# Real LLMs sometimes wrap the SQL in a markdown fence, or keep generating past the SQL
# and echo the few-shot template's own "SQL: ... Analyst report style: ..." structure back
# into the same completion. Either leftover breaks sqlglot parsing, so it must be trimmed
# before the string is treated as SQL.
_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_TRAILING_PROSE_RE = re.compile(r"\n\s*(analyst report style|business takeaway|question)\s*:", re.IGNORECASE)


def _extract_sql(content: str) -> str:
    fenced = _FENCE_RE.search(content)
    if fenced:
        content = fenced.group(1)
    else:
        trailing = _TRAILING_PROSE_RE.search(content)
        if trailing:
            content = content[: trailing.start()]
    return content.strip()


def generate_sql(question: str, few_shots: str, llm) -> str:
    if hasattr(llm, "generate_sql"):
        return _extract_sql(llm.generate_sql(question, few_shots))
    prompt = f"""Generate exactly one BigQuery SELECT statement for thelook_ecommerce.
Allowed tables: orders, order_items, products, users, distribution_centers.
Do not include DDL/DML. Use fully-qualified table names.
Never project customer PII or quasi-PII columns in SELECT output: email, phone, street_address, postal_code.
If PII is needed only for filtering or joining, use it internally but return non-identifying aggregates or ids only.
Respond with only the SQL statement and nothing else: no markdown fences, no commentary,
and do not repeat the report-style text from the few-shot examples below.
Few-shot examples:
{few_shots}
Question: {question}
SQL:"""
    content = llm.invoke(prompt).content
    if isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict) and "text" in block:
                text += block["text"]
            elif isinstance(block, str):
                text += block
            else:
                text += str(block)
        content = text
    return _extract_sql(content)

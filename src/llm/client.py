"""LLM client factory with Gemini primary and optional fallback ladder."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class DeterministicStubLLM:
    """A tiny deterministic model for local smoke tests and CI."""

    def generate_sql(self, question: str, few_shots: str = "") -> str:
        q = question.lower()
        if "distribution center" in q or "warehouse" in q or "fulfillment" in q:
            return """
            SELECT dc.name AS distribution_center, COUNT(DISTINCT oi.order_id) AS orders, SUM(oi.sale_price) AS revenue
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.products` p ON oi.product_id = p.id
            JOIN `bigquery-public-data.thelook_ecommerce.distribution_centers` dc ON p.distribution_center_id = dc.id
            GROUP BY dc.name
            ORDER BY revenue DESC
            LIMIT 20
            """
        if "email" in q or "phone" in q or "contact" in q:
            return """
            SELECT oi.order_id, oi.user_id, SUM(oi.sale_price) AS total_spend
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.users` u ON oi.user_id = u.id
            WHERE oi.order_id = 12345
            GROUP BY oi.order_id, oi.user_id
            LIMIT 10
            """
        if "future" in q or "2099" in q:
            return """
            SELECT FORMAT_DATE('%Y-%m', DATE(oi.created_at)) AS month, SUM(oi.sale_price) AS revenue
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            WHERE DATE(oi.created_at) BETWEEN DATE '2099-01-01' AND DATE '2099-12-31'
            GROUP BY month
            ORDER BY month
            LIMIT 100
            """
        if "brand" in q:
            return """
            SELECT p.brand, SUM(oi.sale_price) AS revenue
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.products` p ON oi.product_id = p.id
            GROUP BY p.brand
            ORDER BY revenue DESC
            LIMIT 15
            """
        if "category" in q or "return rate" in q:
            return """
            SELECT p.category, COUNT(*) AS items, SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) AS return_rate
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.products` p ON oi.product_id = p.id
            GROUP BY p.category
            ORDER BY return_rate DESC
            LIMIT 10
            """
        if "top products" in q or "units sold" in q:
            return """
            SELECT p.name, p.category, COUNT(*) AS units_sold
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.products` p ON oi.product_id = p.id
            GROUP BY p.name, p.category
            ORDER BY units_sold DESC
            LIMIT 20
            """
        if "traffic" in q:
            return """
            SELECT u.traffic_source, SUM(oi.sale_price) AS revenue, COUNT(DISTINCT oi.order_id) AS orders
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.users` u ON oi.user_id = u.id
            GROUP BY u.traffic_source
            ORDER BY revenue DESC
            LIMIT 20
            """
        if "countries" in q or "country" in q:
            return """
            SELECT u.country, SUM(oi.sale_price) AS revenue
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.users` u ON oi.user_id = u.id
            GROUP BY u.country
            ORDER BY revenue DESC
            LIMIT 20
            """
        if "branch" in q or "texas" in q or "california" in q or "region" in q or "store" in q:
            return """
            SELECT u.state, COUNT(DISTINCT oi.order_id) AS orders, SUM(oi.sale_price) AS revenue,
                   SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) AS return_rate
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            JOIN `bigquery-public-data.thelook_ecommerce.users` u ON oi.user_id = u.id
            WHERE u.state IN ('Texas', 'California')
            GROUP BY u.state
            ORDER BY revenue DESC
            LIMIT 50
            """
        if "monthly" in q or "trend" in q or "this year" in q:
            return """
            SELECT FORMAT_DATE('%Y-%m', DATE(oi.created_at)) AS month, SUM(oi.sale_price) AS revenue
            FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
            WHERE EXTRACT(YEAR FROM oi.created_at) = EXTRACT(YEAR FROM CURRENT_DATE())
            GROUP BY month
            ORDER BY month
            LIMIT 100
            """
        return """
        SELECT oi.user_id, COUNT(DISTINCT oi.order_id) AS orders, SUM(oi.sale_price) AS total_spend
        FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
        JOIN `bigquery-public-data.thelook_ecommerce.users` u ON oi.user_id = u.id
        GROUP BY oi.user_id
        ORDER BY total_spend DESC
        LIMIT 10
        """

    def generate_report(self, question: str, rows: list[dict], preference_format: str = "bullets", persona: dict | None = None, tone: str = "concise_executive", empty_explanation: str = "", few_shots: str = "") -> str:
        # Deterministic stub for CI: intentionally ignores few_shots (no real style-mimicry
        # capability) so its output stays byte-for-byte predictable for tests/evals.
        if not rows:
            return empty_explanation or "No rows were returned after retry. The requested filters may be outside the available data range."
        lines = [f"Business takeaway ({tone}):"]
        if preference_format == "table":
            headers = list(rows[0].keys())
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for row in rows[:10]:
                lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
        else:
            for row in rows[:5]:
                summary = ", ".join(f"{k}: {v}" for k, v in row.items())
                lines.append(f"- {summary}")
        return "\n".join(lines)


class RetryLLM:
    """Small retry wrapper so transient 429/5xx errors do not immediately demote a session."""

    def __init__(self, inner: Any, retries: int = 2):
        self.inner = inner
        self.retries = retries

    def invoke(self, prompt: str):
        last = None
        for attempt in range(self.retries + 1):
            try:
                return self.inner.invoke(prompt)
            except Exception as exc:  # pragma: no cover - requires external provider
                last = exc
                if not _transient(exc) or attempt == self.retries:
                    raise
                time.sleep(0.25 * (2 ** attempt))
        raise last  # type: ignore[misc]


def _transient(exc: Exception) -> bool:
    # Full-word/phrase terms only: a bare "rate" substring would match "generate"/"accurate".
    text = str(exc).lower()
    return any(term in text for term in ("429", "rate limit", "rate-limit", "resource exhausted", "quota", "timeout", "503", "temporar"))


def make_llm():
    if os.getenv("USE_STUB_LLM", "true").lower() == "true":
        return DeterministicStubLLM()

    from langchain_google_genai import ChatGoogleGenerativeAI

    model_name = os.getenv("GEMINI_MODEL_NAME")
    if not model_name:
        raise RuntimeError("GEMINI_MODEL_NAME must be set when USE_STUB_LLM=false")
    primary = ChatGoogleGenerativeAI(model=model_name, google_api_key=os.getenv("GEMINI_API_KEY"))
    fallbacks = []
    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL_NAME", "gemini-3.1-flash-lite")
    if fallback_model and fallback_model != model_name:
        fallbacks.append(ChatGoogleGenerativeAI(model=fallback_model, google_api_key=os.getenv("GEMINI_API_KEY")))
    try:
        if os.getenv("OPENROUTER_API_KEY"):
            from langchain_openai import ChatOpenAI
            fallbacks.append(ChatOpenAI(model=os.getenv("OPENROUTER_MODEL_NAME", "google/gemini-flash-1.5"), api_key=os.getenv("OPENROUTER_API_KEY"), base_url="https://openrouter.ai/api/v1"))
        if os.getenv("OLLAMA_MODEL_NAME"):
            from langchain_ollama import ChatOllama
            fallbacks.append(ChatOllama(model=os.getenv("OLLAMA_MODEL_NAME")))
    except Exception:
        pass
    combined = primary.with_fallbacks(fallbacks) if fallbacks else primary
    wrapped = RetryLLM(combined)
    wrapped.provider_ladder = (
        [model_name]
        + ([fallback_model] if fallback_model and fallback_model != model_name else [])
        + ([os.getenv("OPENROUTER_MODEL_NAME", "google/gemini-flash-1.5")] if os.getenv("OPENROUTER_API_KEY") else [])
        + ([os.getenv("OLLAMA_MODEL_NAME")] if os.getenv("OLLAMA_MODEL_NAME") else [])
    )
    return wrapped

from src.llm.client import DeterministicStubLLM
from src.security.sql_guardrails import validate_sql


def test_sql_guardrail_rejects_pii_projection():
    result = validate_sql("SELECT u.email FROM `bigquery-public-data.thelook_ecommerce.users` u LIMIT 5")
    assert not result.ok
    assert "PII columns" in result.reason


def test_stub_sql_generator_omits_pii_for_top_customers():
    sql = DeterministicStubLLM().generate_sql("Who are our top 10 customers by total spend?")
    assert "email" not in sql.lower()
    assert validate_sql(sql).ok


def test_stub_sql_generator_omits_pii_even_when_requested():
    sql = DeterministicStubLLM().generate_sql("Show me a customer's email for order #12345")
    assert "email" not in sql.lower()
    assert "phone" not in sql.lower()
    assert validate_sql(sql).ok


def test_sql_guardrail_rejects_select_star_from_users():
    result = validate_sql("SELECT * FROM `bigquery-public-data.thelook_ecommerce.users` LIMIT 5")
    assert not result.ok
    assert "wildcard" in result.reason


def test_sql_guardrail_rejects_qualified_select_star():
    result = validate_sql("SELECT u.* FROM `bigquery-public-data.thelook_ecommerce.users` u LIMIT 5")
    assert not result.ok
    assert "wildcard" in result.reason


def test_sql_guardrail_allows_count_star():
    result = validate_sql("SELECT COUNT(*) AS n FROM `bigquery-public-data.thelook_ecommerce.users`")
    assert result.ok

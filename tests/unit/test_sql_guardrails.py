from src.security.sql_guardrails import validate_sql


def test_allows_single_select_allowlisted():
    result = validate_sql("SELECT order_id FROM `bigquery-public-data.thelook_ecommerce.orders` LIMIT 1")
    assert result.ok
    assert result.tables == frozenset({"orders"})


def test_rejects_any_semicolon_or_multi_statement():
    result = validate_sql("SELECT order_id FROM orders; SELECT id FROM users")
    assert not result.ok
    assert "semicolons" in result.reason


def test_rejects_dml():
    result = validate_sql("DELETE FROM orders WHERE 1=1")
    assert not result.ok


def test_rejects_unknown_table():
    result = validate_sql("SELECT * FROM secret_table")
    assert not result.ok
    assert "non-allowlisted" in result.reason


def test_rejects_wrong_dataset_even_with_allowed_leaf_name():
    result = validate_sql("SELECT id FROM `some-other-project.some_dataset.users` LIMIT 1")
    assert not result.ok
    assert "outside" in result.reason

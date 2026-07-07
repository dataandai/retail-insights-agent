from src.agent.nodes.self_healer import MAX_HEALING_ATTEMPTS, maybe_heal


def test_self_healer_cost_shield_caps_retries():
    retries = 0
    question = "broken query"
    for _ in range(MAX_HEALING_ATTEMPTS):
        result = maybe_heal(question, "SELECT unknown_column FROM orders", "Unrecognized name", False, retries, None)
        assert result["should_retry"]
        retries = result["retries"]
    stopped = maybe_heal(question, "SELECT unknown_column FROM orders", "Unrecognized name", False, retries, None)
    assert not stopped["should_retry"]
    assert stopped["retries"] == MAX_HEALING_ATTEMPTS


def test_exhausted_budget_message_contains_last_error():
    stopped = maybe_heal("q", "SELECT unknown_column FROM orders", "Unrecognized name: unknown_column", False, MAX_HEALING_ATTEMPTS, None)
    assert not stopped["should_retry"]
    assert "Unrecognized name: unknown_column" in stopped["message"]


def test_empty_result_explains_data_range_after_budget():
    stopped = maybe_heal(
        "future range",
        "SELECT 1 FROM order_items",
        "",
        True,
        MAX_HEALING_ATTEMPTS,
        None,
        data_bounds={"table": "order_items", "column": "created_at", "min_value": "2019-01-01", "max_value": "2024-12-31"},
    )
    assert not stopped["should_retry"]
    assert "2019-01-01" in stopped["message"]

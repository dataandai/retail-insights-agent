from evaluation.run_evals import compare_result_sets


def test_compare_result_sets_detects_swapped_categories():
    reference = [
        {"state": "Texas", "revenue": 100.0},
        {"state": "California", "revenue": 200.0},
    ]
    swapped = [
        {"state": "Texas", "revenue": 200.0},
        {"state": "California", "revenue": 100.0},
    ]
    assert not compare_result_sets(swapped, reference)


def test_compare_result_sets_ignores_row_order_and_allows_tolerance():
    reference = [
        {"month": "2024-01", "revenue": 100.0000001},
        {"month": "2024-02", "revenue": 200.0},
    ]
    agent = [
        {"month": "2024-02", "revenue": 200.0},
        {"month": "2024-01", "revenue": 100.0000002},
    ]
    assert compare_result_sets(agent, reference, tolerance=1e-5)

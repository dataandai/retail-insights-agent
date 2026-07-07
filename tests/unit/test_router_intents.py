from src.agent.nodes.router import route_intent


def test_email_report_is_unsupported_not_branch_or_pii():
    route = route_intent("How do I email a report to my regional store manager?")
    assert route["intent"] == "unsupported_action"


def test_remove_reports_routes_to_delete():
    route = route_intent("Remove reports mentioning Acme Corp")
    assert route["intent"] == "delete_report"


def test_delete_request_mentioning_schema_routes_to_delete():
    route = route_intent("Delete my reports about the schema migration")
    assert route["intent"] == "delete_report"


def test_plain_schema_question_routes_to_schema():
    assert route_intent("Show me the schema")["intent"] == "schema"
    assert route_intent("/schema")["intent"] == "schema"
    assert route_intent("What columns are available?")["intent"] == "schema"


def test_store_manager_phrase_does_not_trigger_branch_mapping():
    route = route_intent("Show revenue for customers managed by my store manager")
    assert route["intent"] == "analysis"
    assert "branch_disclosure" not in route

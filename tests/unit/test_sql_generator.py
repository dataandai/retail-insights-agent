from src.agent.nodes.sql_generator import generate_sql


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChatModel:
    def __init__(self, content):
        self._content = content

    def invoke(self, prompt: str):
        return _FakeMessage(self._content)


def test_generate_sql_strips_markdown_fence():
    llm = _FakeChatModel("```sql\nSELECT 1 FROM orders\n```")
    assert generate_sql("q", "", llm) == "SELECT 1 FROM orders"


def test_generate_sql_trims_leaked_few_shot_report_text():
    llm = _FakeChatModel(
        "SELECT u.state, SUM(oi.sale_price) AS revenue\n"
        "FROM `bigquery-public-data.thelook_ecommerce.order_items` oi\n"
        "JOIN `bigquery-public-data.thelook_ecommerce.users` u ON oi.user_id = u.id\n"
        "WHERE u.state IN ('Texas', 'California')\n"
        "GROUP BY u.state\n"
        "ORDER BY revenue DESC\n"
        "\n"
        "Analyst report style:\n"
        "Disclose branch=customer state, then compare revenue, order count, and return rate."
    )
    sql = generate_sql("q", "", llm)
    assert "Analyst report style" not in sql
    assert sql.strip().endswith("ORDER BY revenue DESC")


def test_generate_sql_handles_multi_part_content_blocks():
    llm = _FakeChatModel([{"text": "SELECT 1 "}, {"text": "FROM orders"}])
    assert generate_sql("q", "", llm) == "SELECT 1 FROM orders"


def test_generate_sql_passthrough_when_already_clean():
    llm = _FakeChatModel("SELECT 1 FROM orders")
    assert generate_sql("q", "", llm) == "SELECT 1 FROM orders"

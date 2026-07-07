from google.cloud import bigquery as bq

from src.agent.nodes.executor import execute_sql
from src.database.bigquery_runner import BigQueryRunner, MockBigQueryRunner


class _FakeQueryJob:
    def __init__(self, total_bytes_processed=100, rows=None):
        self.total_bytes_processed = total_bytes_processed
        self._rows = rows if rows is not None else []

    def result(self, max_results=None):
        return list(self._rows)


class _FlakyBQClient:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.attempts = 0

    def query(self, sql, job_config=None, location=None):
        if getattr(job_config, "dry_run", False):
            return _FakeQueryJob(total_bytes_processed=100)
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise Exception("503 Service Unavailable")
        return _FakeQueryJob(total_bytes_processed=100, rows=[{"id": 1}])


class _AlwaysFailBQ:
    def __init__(self):
        self.attempts = 0

    def query(self, sql, job_config=None, location=None):
        if getattr(job_config, "dry_run", False):
            return _FakeQueryJob(total_bytes_processed=100)
        self.attempts += 1
        raise Exception("permission denied on dataset")


def _make_runner(client) -> BigQueryRunner:
    runner = object.__new__(BigQueryRunner)
    runner.bigquery = bq
    runner.max_bytes_billed = 200000000
    runner.max_rows = 100
    runner.location = "US"
    runner.client = client
    return runner


def test_execute_recovers_from_transient_errors_and_reports_true_attempt_count():
    runner = _make_runner(_FlakyBQClient(fail_times=2))
    result = runner.execute("SELECT id FROM order_items")
    assert not result.error
    assert result.rows == [{"id": 1}]
    assert runner.client.attempts == 3
    assert result.metadata["attempts"] == 3


def test_execute_fails_fast_on_permanent_error_with_accurate_attempt_count():
    runner = _make_runner(_AlwaysFailBQ())
    result = runner.execute("SELECT id FROM order_items")
    assert result.error
    assert runner.client.attempts == 1, "a non-transient error must not be retried 3 times"
    assert result.metadata["attempts"] == 1, "reported attempt count must match what actually happened"


def test_mock_runner_rejects_over_budget_query_before_materializing_rows():
    runner = MockBigQueryRunner()
    result = execute_sql("SELECT force_expensive FROM order_items", runner)
    assert result["error"] and "exceed" in result["error"].lower()
    assert result["rows"] == []

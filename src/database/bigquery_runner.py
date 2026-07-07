"""Safe BigQuery runner with dry-run, cost cap, row cap, and live schema checks."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from src.security.pii_patterns import configure_pii_columns, mask_records


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    bytes_estimate: int = 0
    sql: str = ""
    error: str | None = None
    empty: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimestampBounds:
    table: str
    column: str
    min_value: str | None
    max_value: str | None


class Runner(Protocol):
    def dry_run(self, sql: str) -> int: ...
    def execute(self, sql: str) -> QueryResult: ...
    def introspect_schema(self) -> dict[str, list[str]]: ...
    def timestamp_bounds(self, table: str, column: str = "created_at") -> TimestampBounds: ...


class BigQueryRunner:
    DATASET = "bigquery-public-data.thelook_ecommerce"
    TABLES = ["orders", "order_items", "products", "users", "distribution_centers"]

    def __init__(self, *, project: str | None = None, max_bytes_billed: int | None = None, max_rows: int | None = None, location: str | None = None):
        from google.cloud import bigquery  # imported lazily so unit tests do not require GCP libs

        self.bigquery = bigquery
        self.client = bigquery.Client(project=project or os.getenv("GOOGLE_CLOUD_PROJECT"))
        self.max_bytes_billed = int(max_bytes_billed or os.getenv("MAX_BYTES_BILLED", "200000000"))
        self.max_rows = int(max_rows or os.getenv("MAX_ROWS_RETURNED", "100"))
        self.location = location or os.getenv("BQ_LOCATION", "US")

    def dry_run(self, sql: str) -> int:
        job_config = self.bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        query_job = self.client.query(sql, job_config=job_config, location=self.location)
        return int(query_job.total_bytes_processed or 0)

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        text = str(exc).lower()
        # Phrase-level terms: a bare "500" or "internal" would match byte counts and
        # unrelated messages, retrying (and re-billing) queries that can never succeed.
        transient_terms = ("rate limit", "quota", "timeout", "temporar", "backenderror", "internal error", "error 500", "http 500", "503", "429")
        return any(term in text for term in transient_terms)

    def execute(self, sql: str) -> QueryResult:
        try:
            estimate = self.dry_run(sql)
            if estimate > self.max_bytes_billed:
                return QueryResult(rows=[], bytes_estimate=estimate, sql=sql, error=f"estimated bytes {estimate} exceed MAX_BYTES_BILLED={self.max_bytes_billed}")
        except Exception as exc:
            return QueryResult(rows=[], sql=sql, error=f"dry-run failed: {exc}")

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                job_config = self.bigquery.QueryJobConfig(maximum_bytes_billed=self.max_bytes_billed, use_query_cache=True)
                query_job = self.client.query(sql, job_config=job_config, location=self.location)
                rows_iter = query_job.result(max_results=self.max_rows)
                raw_rows = [dict(row.items()) for row in rows_iter]
                # Primary PII protection: rows are redacted immediately after materialization,
                # before any caller can serialize them into an LLM prompt.
                rows = mask_records(raw_rows)
                return QueryResult(rows=rows, bytes_estimate=estimate, sql=sql, empty=(len(rows) == 0), metadata={"attempts": attempt + 1})
            except Exception as exc:  # pragma: no cover - exercised with real BQ failures
                last_error = exc
                if not self._is_transient_error(exc) or attempt == 2:
                    break
                time.sleep(0.25 * (2 ** attempt))
        return QueryResult(rows=[], sql=sql, error=str(last_error), metadata={"attempts": attempt + 1})

    def introspect_schema(self) -> dict[str, list[str]]:
        schema: dict[str, list[str]] = {}
        for table in self.TABLES:
            sql = f"""
            SELECT column_name
            FROM `{self.DATASET}.INFORMATION_SCHEMA.COLUMNS`
            WHERE table_name = '{table}'
            ORDER BY ordinal_position
            """
            result = self.client.query(sql, location=self.location).result(max_results=200)
            schema[table] = [row["column_name"] for row in result]
        configure_pii_columns(schema)
        return schema

    def timestamp_bounds(self, table: str, column: str = "created_at") -> TimestampBounds:
        if table not in self.TABLES:
            return TimestampBounds(table=table, column=column, min_value=None, max_value=None)
        sql = f"SELECT CAST(MIN({column}) AS STRING) AS min_value, CAST(MAX({column}) AS STRING) AS max_value FROM `{self.DATASET}.{table}`"
        try:
            row = next(iter(self.client.query(sql, location=self.location).result(max_results=1)), None)
            if not row:
                return TimestampBounds(table=table, column=column, min_value=None, max_value=None)
            return TimestampBounds(table=table, column=column, min_value=row["min_value"], max_value=row["max_value"])
        except Exception:
            return TimestampBounds(table=table, column=column, min_value=None, max_value=None)


class MockBigQueryRunner:
    """Deterministic local runner for smoke tests without BigQuery credentials."""
    def __init__(self):
        self.schema = {
            "orders": ["order_id", "user_id", "status", "gender", "created_at", "returned_at", "shipped_at", "delivered_at", "num_of_item"],
            "order_items": ["id", "order_id", "user_id", "product_id", "inventory_item_id", "status", "created_at", "shipped_at", "delivered_at", "returned_at", "sale_price"],
            "products": ["id", "cost", "category", "name", "brand", "retail_price", "department", "sku", "distribution_center_id"],
            "users": ["id", "first_name", "last_name", "email", "age", "gender", "state", "street_address", "postal_code", "city", "country", "latitude", "longitude", "traffic_source", "created_at"],
            "distribution_centers": ["id", "name", "latitude", "longitude"],
        }

    def dry_run(self, sql: str) -> int:
        if "force_expensive" in sql.lower():
            return int(os.getenv("MAX_BYTES_BILLED", "200000000")) + 1
        return 123456

    def execute(self, sql: str) -> QueryResult:
        estimate = self.dry_run(sql)
        if estimate > int(os.getenv("MAX_BYTES_BILLED", "200000000")):
            return QueryResult(rows=[], bytes_estimate=estimate, sql=sql, error="estimated bytes exceed MAX_BYTES_BILLED")
        lower = sql.lower()
        if "future" in lower or "2099" in lower:
            return QueryResult(rows=[], bytes_estimate=estimate, sql=sql, empty=True)
        if "unknown_column" in lower:
            return QueryResult(rows=[], bytes_estimate=estimate, sql=sql, error="Unrecognized name: unknown_column")
        if "distribution_centers" in lower:
            rows = [
                {"distribution_center": "Chicago IL", "revenue": 210000.0, "orders": 1700},
                {"distribution_center": "Houston TX", "revenue": 168000.0, "orders": 1400},
            ]
        elif "state" in lower and ("texas" in lower or "california" in lower or "tx" in lower):
            rows = [
                {"state": "California", "revenue": 250000.0, "orders": 1800, "return_rate": 0.06},
                {"state": "Texas", "revenue": 190000.0, "orders": 1600, "return_rate": 0.11},
            ]
        elif "extract(month" in lower or "format_date" in lower or "date_trunc" in lower or "monthly" in lower:
            rows = [
                {"month": "2026-01", "revenue": 120000.0},
                {"month": "2026-02", "revenue": 132500.0},
                {"month": "2026-03", "revenue": 128100.0},
            ]
        elif "order_id = 12345" in lower or "order_id=12345" in lower:
            rows = [{"order_id": 12345, "user_id": 123, "total_spend": 99.0}]
        elif "brand" in lower:
            rows = [{"brand": "Acme Denim", "revenue": 220000.0}, {"brand": "Urban Fit", "revenue": 195000.0}]
        elif "traffic_source" in lower:
            rows = [{"traffic_source": "Search", "revenue": 310000.0, "orders": 2400}, {"traffic_source": "Email", "revenue": 175000.0, "orders": 1300}]
        elif "country" in lower:
            rows = [{"country": "United States", "revenue": 500000.0}, {"country": "United Kingdom", "revenue": 120000.0}]
        elif "category" in lower and "return_rate" in lower:
            rows = [{"category": "Dresses", "items": 3200, "return_rate": 0.14}, {"category": "Jeans", "items": 4100, "return_rate": 0.09}]
        elif "category" in lower:
            rows = [{"category": "Jeans", "revenue": 260000.0}, {"category": "Dresses", "revenue": 235000.0}]
        elif "units_sold" in lower or "top products" in lower:
            rows = [{"name": "Classic Tee", "category": "Tops", "units_sold": 430}, {"name": "Slim Jeans", "category": "Jeans", "units_sold": 390}]
        else:
            rows = [
                {"user_id": 101, "total_spend": 1022.50, "orders": 12},
                {"user_id": 202, "total_spend": 980.00, "orders": 10},
            ]
        return QueryResult(rows=mask_records(rows), bytes_estimate=estimate, sql=sql, empty=(len(rows) == 0), metadata={"attempts": 1})

    def introspect_schema(self) -> dict[str, list[str]]:
        configure_pii_columns(self.schema)
        return self.schema

    def timestamp_bounds(self, table: str, column: str = "created_at") -> TimestampBounds:
        return TimestampBounds(table=table, column=column, min_value="2019-01-01", max_value="2024-12-31")


def make_runner() -> Runner:
    if os.getenv("USE_MOCK_BQ", "true").lower() == "true":
        return MockBigQueryRunner()
    return BigQueryRunner()


def reconcile_schema_with_notes(schema: dict[str, list[str]], notes_path: str | Path = "config/schema_notes.yaml") -> list[str]:
    """Compare live/introspected schema with curated notes and return warnings."""
    path = Path(notes_path)
    if not path.exists():
        return [f"schema notes not found: {path}"]
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    warnings: list[str] = []
    configured = raw.get("allowed_tables", {})
    for table, meta in configured.items():
        expected = set(meta.get("expected_columns", []))
        live = set(schema.get(table, []))
        # orders has known singular/plural drift; accept either variant without warning.
        if table == "orders" and ({"num_of_item", "num_of_items"} & live):
            expected = expected - {"num_of_item", "num_of_items"}
            live_for_compare = live - {"num_of_item", "num_of_items"}
        else:
            live_for_compare = live
        missing = sorted(expected - live_for_compare)
        unexpected = sorted(live_for_compare - expected)
        if missing:
            warnings.append(f"{table}: expected columns missing from live schema: {missing}")
        if unexpected:
            warnings.append(f"{table}: live schema has columns not in notes: {unexpected}")
    for table in schema:
        if table not in configured:
            warnings.append(f"{table}: live table not present in schema_notes.yaml")
    return warnings

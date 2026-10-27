"""Actual disposable PostgreSQL integration, enabled in CI's trust-only service."""
import os

import pytest

from aheaddb import Event, Schema, Snapshot


def test_postgres_consistent_bounded_readonly_snapshot():
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("AHEAD_TEST_POSTGRES")
    if not dsn:
        pytest.skip("No explicitly configured disposable PostgreSQL integration service")
    schema = Schema(entity_table="ahead_entities", events=(Event(table="ahead_events", values=("amount",)),))
    with psycopg.connect(dsn, autocommit=True) as conn:
        # This test writes only to its explicitly configured disposable test service.
        conn.execute("CREATE TABLE ahead_entities(entity_id text PRIMARY KEY, available_at timestamptz)")
        conn.execute("CREATE TABLE ahead_events(id text PRIMARY KEY, entity_id text, occurred_at timestamptz, available_at timestamptz, amount numeric)")
        conn.execute("INSERT INTO ahead_entities VALUES ('a','2024-01-01')")
        conn.execute("INSERT INTO ahead_events VALUES ('e','a','2024-01-02','2024-01-03',2.5)")
        before = conn.execute("SELECT amount FROM ahead_events").fetchone()[0]
        snapshot = Snapshot.postgres(conn, schema)
        assert snapshot.table("ahead_events").amount.iloc[0] == 2.5
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert conn.execute("SELECT amount FROM ahead_events").fetchone()[0] == before
        with pytest.raises(ValueError, match="row limit"):
            Snapshot.postgres(conn, schema, max_rows=1)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        with conn.transaction():
            with pytest.raises(ValueError, match="idle"):
                Snapshot.postgres(conn, schema)

        class ReadonlyProbe:
            """Attempt a write inside the adapter transaction before its first SELECT."""
            info = conn.info

            def transaction(self):
                return conn.transaction()

            def execute(self, sql, args=None):
                if sql.startswith("SELECT"):
                    conn.execute("UPDATE ahead_events SET amount=999")
                return conn.execute(sql, args)

        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            Snapshot.postgres(ReadonlyProbe(), schema)
        assert conn.execute("SELECT amount FROM ahead_events").fetchone()[0] == before
        conn.execute("DROP TABLE ahead_events,ahead_entities")

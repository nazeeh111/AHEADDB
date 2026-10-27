"""Read-only, bounded source ingestion. Snapshots own defensive copies."""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .spec import Schema, digest


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class Snapshot:
    def __init__(self, tables: dict[str, pd.DataFrame], schema: Schema, max_rows: int = 2_000_000):
        self.schema = schema
        required = {schema.entity_table, *(e.table for e in schema.events)}
        if not required <= tables.keys():
            raise ValueError(f"Missing tables: {sorted(required - tables.keys())}")
        self._tables = {k: tables[k].copy(deep=True) for k in sorted(required)}
        if sum(map(len, self._tables.values())) > max_rows:
            raise ValueError(f"Snapshot exceeds {max_rows} row limit")
        entity = self._tables[schema.entity_table]
        self._validate(entity, schema.entity_key, [schema.available_at])
        entity[schema.entity_key] = entity[schema.entity_key].astype(str)
        valid_ids = set(entity[schema.entity_key])
        for e in schema.events:
            frame = self._tables[e.table]
            self._validate(frame, e.key, [e.occurred_at, e.available_at])
            if e.entity_key not in frame or frame[e.entity_key].isna().any():
                raise ValueError(f"Missing/null foreign key {e.table}.{e.entity_key}")
            frame[e.key] = frame[e.key].astype(str)
            frame[e.entity_key] = frame[e.entity_key].astype(str)
            if not set(frame[e.entity_key]) <= valid_ids:
                raise ValueError(f"Orphan foreign keys in {e.table}")
            for col in e.values:
                if col not in frame or not pd.api.types.is_numeric_dtype(frame[col]):
                    raise ValueError(f"Feature {e.table}.{col} must be numeric")
                if np.isinf(frame[col].to_numpy(dtype=float)).any():
                    raise ValueError(f"Infinite values in {e.table}.{col}")
        # Compute once at ingestion. Refresh compares immutable row indexes rather
        # than repeatedly serializing and hashing the same source bytes.
        self._hashes = {k: self._compute_row_hashes(k) for k in self._tables}
        self.version = digest({k: v.to_dict() for k, v in self._hashes.items()})

    @staticmethod
    def _validate(frame: pd.DataFrame, key: str, times: list[str]) -> None:
        if frame.columns.duplicated().any():
            raise ValueError("Duplicate column names")
        if key not in frame or frame[key].isna().any() or frame[key].astype(str).duplicated().any():
            raise ValueError(f"Missing/null/duplicate primary key {key}")
        for c in times:
            if c not in frame:
                raise ValueError(f"Missing time column {c}")
            frame[c] = pd.to_datetime(frame[c], utc=True, errors="raise")
            if frame[c].isna().any():
                raise ValueError(f"Null time in {c}")

    def table(self, name: str) -> pd.DataFrame:
        return self._tables[name].copy(deep=True)

    def row_hashes(self, name: str) -> pd.Series:
        return self._hashes[name].copy()

    def _compute_row_hashes(self, name: str) -> pd.Series:
        key = (self.schema.entity_key if name == self.schema.entity_table else
               next(e.key for e in self.schema.events if e.table == name))
        df = self._tables[name].sort_index(axis=1).set_index(key)
        # SHA256 per row avoids relying on the collision resistance of pandas' 64-bit hash.
        return df.apply(lambda row: digest(row.to_dict()), axis=1).sort_index()

    @classmethod
    def duckdb(cls, path: str | Path, schema: Schema, max_rows: int = 2_000_000) -> Snapshot:
        path = Path(path).resolve(strict=True)
        with duckdb.connect(str(path), read_only=True, config={
            "enable_external_access": "false", "threads": "2", "memory_limit": "512MB"
        }) as conn:
            tables = {}
            remaining = max_rows
            for name in [schema.entity_table, *(e.table for e in schema.events)]:
                tables[name] = conn.execute(f"SELECT * FROM {quote(name)} LIMIT {remaining + 1}").df()
                remaining -= len(tables[name])
                if remaining < 0:
                    raise ValueError("Snapshot row limit exceeded")
        return cls(tables, schema, max_rows)

    @classmethod
    def parquet(cls, paths: dict[str, str | Path], schema: Schema, max_rows: int = 2_000_000) -> Snapshot:
        tables = {}
        remaining = max_rows
        with duckdb.connect(config={"threads": "2", "memory_limit": "512MB"}) as conn:
            for name in [schema.entity_table, *(e.table for e in schema.events)]:
                path = Path(paths[name]).resolve(strict=True)
                # Only an explicitly supplied local regular file, bound as a parameter.
                if not path.is_file() or path.suffix.lower() != ".parquet":
                    raise ValueError("Expected a local .parquet file")
                tables[name] = conn.execute("SELECT * FROM read_parquet(?) LIMIT ?", [str(path), remaining + 1]).df()
                remaining -= len(tables[name])
                if remaining < 0:
                    raise ValueError("Snapshot row limit exceeded")
        return cls(tables, schema, max_rows)

    @classmethod
    def postgres(cls, connection, schema: Schema, max_rows: int = 2_000_000) -> Snapshot:
        """Read one consistent snapshot from a caller-owned psycopg3 connection.

        No credentials are accepted/stored by AHEADDB. The connection must be idle;
        the method opens an explicitly read-only transaction and leaves it idle.
        """
        try:
            from psycopg.pq import TransactionStatus
        except ImportError as exc:
            raise RuntimeError("Install aheaddb[postgres] for PostgreSQL snapshots") from exc
        if connection.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError("PostgreSQL connection must be idle before a read-only snapshot")
        tables = {}
        remaining = max_rows
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '10000ms'")
            connection.execute("SET LOCAL lock_timeout = '1000ms'")
            for name in [schema.entity_table, *(e.table for e in schema.events)]:
                cursor = connection.execute(f"SELECT * FROM {quote(name)} LIMIT %s", [remaining + 1])
                tables[name] = pd.DataFrame(cursor.fetchall(), columns=[c.name for c in cursor.description])
                remaining -= len(tables[name])
                if remaining < 0:
                    raise ValueError("Snapshot row limit exceeded")
        for event in schema.events:
            for col in event.values:
                if col in tables[event.table]:
                    tables[event.table][col] = pd.to_numeric(tables[event.table][col], errors="raise")
        return cls(tables, schema, max_rows)

    def inspect(self) -> dict:
        return {"data_version": self.version, "schema": self.schema.model_dump(), "tables": {
            k: {"rows": len(v), "columns": {c: str(d) for c, d in v.dtypes.items()}}
            for k, v in self._tables.items()
        }}

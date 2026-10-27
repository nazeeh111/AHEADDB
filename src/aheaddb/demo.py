"""SignalDesk: a synthetic shop with real fitted return/revenue predictions."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .snapshot import Snapshot
from .spec import Event, Outcome, Schema, Task


def demo_tables(entities: int = 160, days: int = 240, seed: int = 19) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-01", tz="UTC")
    customers = pd.DataFrame({"entity_id": [f"C{i:04d}" for i in range(entities)], "available_at": start})
    rows: list[tuple] = []
    for i, entity in enumerate(customers.entity_id):
        rate = rng.uniform(0.025, 0.22)
        value = rng.uniform(12, 140)
        for day in range(days):
            # Heterogeneity and gradual changes make historical behavior useful, not perfect.
            if rng.random() < rate * (0.65 + 0.35 * np.cos(day / 50 + i)):
                t = start + pd.Timedelta(days=day, hours=int(rng.integers(0, 23)))
                rows.append((f"O{len(rows):06d}", entity, t, t + pd.Timedelta(hours=1),
                             round(float(value * rng.lognormal(0, 0.3)), 2)))
    orders = pd.DataFrame(rows, columns=["id", "entity_id", "occurred_at", "available_at", "amount"])
    return {"entities": customers, "orders": orders}


def demo_schema() -> Schema:
    return Schema(events=(Event(table="orders", values=("amount",)),))


def demo_tasks() -> list[Task]:
    return [Task(name="return_14d", kind="binary", outcome=Outcome(table="orders"),
                 horizon_days=14, history_days=60, metric="roc_auc"),
            Task(name="revenue_14d", kind="regression", outcome=Outcome(table="orders", aggregation="sum", column="amount"),
                 horizon_days=14, history_days=60, metric="mae")]


def create_demo(destination: Path) -> Path:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    db = destination / "shop.duckdb"
    config = destination / "ahead.json"
    if db.exists() or config.exists():
        raise ValueError("Demo already exists; choose a fresh destination to preserve existing files")
    tables = demo_tables()
    with duckdb.connect(str(db)) as conn:
        for name, df in tables.items():
            conn.register("source", df)
            conn.execute(f'CREATE TABLE "{name}" AS SELECT * FROM source')
            conn.unregister("source")
    data = {"database": db.name, "schema": demo_schema().model_dump(),
            "tasks": [t.model_dump() for t in demo_tasks()], "synthetic_demo": True,
            "training_cutoffs": ["2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"],
            "training_as_of": "2024-07-01", "prediction_cutoff": "2024-07-15",
            "backtest_cutoffs": ["2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01", "2024-07-01", "2024-08-01"],
            "data_as_of": "2024-08-27"}
    config.write_text(json.dumps(data, indent=2) + "\n")
    return config


def demo_snapshot() -> Snapshot:
    return Snapshot(demo_tables(), demo_schema())

"""One explicit local project, shared by CLI, HTTP and MCP."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import Field

from .compiler import Compiler
from .engine import Engine
from .modeling import backtest, fit
from .snapshot import Snapshot
from .spec import Schema, StrictModel, Task, timestamp


class ProjectConfig(StrictModel):
    database: str
    schema_: Schema = Field(alias="schema")
    tasks: tuple[Task, ...]
    training_cutoffs: tuple[str, ...]
    training_as_of: str
    prediction_cutoff: str
    backtest_cutoffs: tuple[str, ...]
    data_as_of: str
    synthetic_demo: bool = False
    backend_by_task: dict[str, Literal["baseline", "histogram"]] = Field(default_factory=dict)


class Project:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve(strict=True)
        self.config = ProjectConfig.model_validate_json(self.path.read_text())
        self.database = (self.path.parent / self.config.database).resolve(strict=True)
        if len({t.name for t in self.config.tasks}) != len(self.config.tasks):
            raise ValueError("Task names must be unique")
        if set(self.config.backend_by_task) - {t.name for t in self.config.tasks}:
            raise ValueError("backend_by_task contains an unknown task name")
        self.engine = Engine(Snapshot.duckdb(self.database, self.config.schema_))
        self.tasks = {t.name: t for t in self.config.tasks}
        self.reports: dict = {}
        self.lock = threading.RLock()
        self.demo_sequence = 0

    def initialize(self) -> None:
        with self.lock:
            for task in self.tasks.values():
                model = fit(self.engine.snapshot, task, list(self.config.training_cutoffs), self.config.training_as_of,
                            self.config.backend_by_task.get(task.name, "histogram"))
                self.engine.materialize(task, model, self.config.prediction_cutoff)

    def plan(self, task: str) -> dict:
        with self.lock:
            return Compiler(self.engine.snapshot).plan(self.tasks[task], self.config.prediction_cutoff)

    def backtest(self, task: str) -> dict:
        with self.lock:
            if task not in self.reports:
                self.reports[task] = backtest(self.engine.snapshot, self.tasks[task],
                                             list(self.config.backtest_cutoffs), self.config.data_as_of)
            return self.reports[task]

    def predictions(self, task: str, limit: int = 1000) -> dict:
        with self.lock:
            result = self.engine.results(task).sort_values("prediction", ascending=False)
            return {"view": self.engine.inspect(task), "predictions": result.head(limit).reset_index().to_dict("records")}

    def refresh(self) -> dict:
        with self.lock:
            receipt = self.engine.refresh(Snapshot.duckdb(self.database, self.config.schema_))
            self.reports.clear()
            return receipt

    def demo_change(self, entity_id: str) -> dict:
        with self.lock:
            if not self.config.synthetic_demo:
                raise ValueError("Demo mutations are disabled for ordinary projects")
            if entity_id not in Compiler(self.engine.snapshot).entities(self.config.prediction_cutoff):
                raise ValueError("Unknown entity")
            tables = {k: self.engine.snapshot.table(k) for k in self.engine.snapshot._tables}
            t = timestamp(self.config.prediction_cutoff) - pd.Timedelta(hours=2)
            self.demo_sequence += 1
            row = pd.DataFrame([{"id": f"DEMO-{self.demo_sequence}", "entity_id": entity_id,
                                 "occurred_at": t, "available_at": t + pd.Timedelta(hours=1), "amount": 450.0}])
            tables["orders"] = pd.concat([tables["orders"], row], ignore_index=True)
            before = {name: float(self.engine.results(name).loc[[entity_id], "prediction"].to_numpy()[0]) for name in self.tasks}
            receipt = self.engine.refresh(Snapshot(tables, self.config.schema_))
            after = {name: float(self.engine.results(name).loc[[entity_id], "prediction"].to_numpy()[0]) for name in self.tasks}
            self.reports.clear()
            return {"entity_id": entity_id, "before": before, "after": after, "refresh": receipt,
                    "scope": "Synthetic in-memory source; original DuckDB file is unchanged"}


def json_text(value: object) -> str:
    return json.dumps(value, indent=2, default=str, allow_nan=False)

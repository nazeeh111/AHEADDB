"""Inspectable contracts. No arbitrary SQL or executable code in a task."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def timestamp(value: str | datetime | pd.Timestamp) -> pd.Timestamp:
    """All public times are instants in UTC; naive times explicitly mean UTC."""
    t = pd.Timestamp(value)
    if pd.isna(t):
        raise ValueError("Time cannot be NaT")
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Event(StrictModel):
    table: str
    key: str = "id"
    entity_key: str = "entity_id"
    occurred_at: str = "occurred_at"
    available_at: str = "available_at"
    values: tuple[str, ...] = ()


class Schema(StrictModel):
    entity_table: str = "entities"
    entity_key: str = "entity_id"
    available_at: str = "available_at"
    events: tuple[Event, ...]

    @model_validator(mode="after")
    def unique_tables(self) -> Schema:
        names = [e.table for e in self.events]
        if len(names) != len(set(names)) or self.entity_table in names or not names:
            raise ValueError("Use distinct entity/event tables and at least one event relation")
        return self


class Outcome(StrictModel):
    table: str
    aggregation: Literal["count", "sum"] = "count"
    column: str | None = None
    threshold: float = 0

    @model_validator(mode="after")
    def sum_column(self) -> Outcome:
        if self.aggregation == "sum" and self.column is None:
            raise ValueError("A sum outcome requires a column")
        return self


class Task(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    kind: Literal["binary", "regression"]
    outcome: Outcome
    horizon_days: int = Field(gt=0, le=3650)
    history_days: int = Field(gt=0, le=36500)
    label_delay_days: int = Field(default=0, ge=0, le=3650)
    metric: Literal["roc_auc", "average_precision", "mae", "rmse"]

    @model_validator(mode="after")
    def metric_kind(self) -> Task:
        if (self.kind == "binary") != (self.metric in {"roc_auc", "average_precision"}):
            raise ValueError("Metric must match task kind")
        return self

    @property
    def version(self) -> str:
        return digest(self.model_dump())

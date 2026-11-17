"""A bounded relational task compiler, shared by training and serving."""
from __future__ import annotations

import math
import statistics
from fractions import Fraction

import numpy as np
import pandas as pd

from .snapshot import Snapshot, quote
from .spec import Task, timestamp


def _finite(value: int | float | Fraction) -> float:
    """Admit only representable binary64 results, never mask arithmetic failure."""
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Numeric aggregate is not representable as finite binary64") from exc
    if not math.isfinite(result):
        raise ValueError("Numeric aggregate is not representable as finite binary64")
    return result


def _numeric_groups(frame: pd.DataFrame, entity_key: str, column: str):
    for key, values in frame.groupby(entity_key, sort=False)[column]:
        yield key, values.dropna().tolist()


def _exact_sum(values: list) -> int | Fraction:
    # Preserve both integer thresholds and exact binary floating input values.
    total = sum((int(v) if isinstance(v, int) else Fraction.from_float(float(v))
                 for v in values), 0)
    return total


def _grouped_sum(frame: pd.DataFrame, entity_key: str, column: str) -> pd.Series:
    return pd.Series({key: _exact_sum(values)
                      for key, values in _numeric_groups(frame, entity_key, column)}, dtype=object)


def _grouped_mean(frame: pd.DataFrame, entity_key: str, column: str) -> pd.Series:
    return pd.Series({key: _finite(Fraction(_exact_sum(values)) / len(values)) if values else 0.0
                      for key, values in _numeric_groups(frame, entity_key, column)}, dtype=float)


def _grouped_std(frame: pd.DataFrame, entity_key: str, column: str) -> pd.Series:
    # pstdev avoids overflow in squared deviations and uses population variance.
    return pd.Series({key: _finite(statistics.pstdev(values)) if values else 0.0
                      for key, values in _numeric_groups(frame, entity_key, column)}, dtype=float)


class Compiler:
    PREPROCESSING = "aggregates-v4:recent1,7,30:last,std,trend:utc:left-open:stable-time-key:exact-binary-sum:finite-admission"

    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self.schema = snapshot.schema

    def validate(self, task: Task) -> None:
        events = {e.table: e for e in self.schema.events}
        if task.outcome.table not in events:
            raise ValueError("Outcome table is not in the declared schema")
        if task.outcome.aggregation == "sum" and task.outcome.column not in events[task.outcome.table].values:
            raise ValueError("Outcome sum column must be a declared numeric value")

    def plan(self, task: Task, cutoff: str) -> dict:
        self.validate(task)
        t = timestamp(cutoff)
        statements = []
        for e in self.schema.events:
            statements.append({"table": e.table, "sql":
                f"SELECT {quote(e.entity_key)}, COUNT(*) AS count FROM {quote(e.table)} "
                f"WHERE {quote(e.occurred_at)} > ? AND {quote(e.occurred_at)} <= ? "
                f"AND {quote(e.available_at)} <= ? GROUP BY {quote(e.entity_key)}",
                "parameters": [str(t - pd.Timedelta(days=task.history_days)), str(t), str(t)]})
        return {"task": task.model_dump(), "cutoff": str(t), "history_queries": statements,
                "aggregates": ["count", "numeric sum/mean/std/last/trend", "numeric missing count",
                               "days since last event", "nested 1/7/30-day count/sum/mean"],
                "label_interval": f"({t}, {t + pd.Timedelta(days=task.horizon_days)}]",
                "label_matures_at": str(t + pd.Timedelta(days=task.horizon_days + task.label_delay_days)),
                "preprocessing": self.PREPROCESSING}

    def entities(self, cutoff: str | pd.Timestamp) -> list[str]:
        df = self.snapshot._tables[self.schema.entity_table]
        return sorted(df.loc[df[self.schema.available_at] <= timestamp(cutoff), self.schema.entity_key])

    def features(self, task: Task, cutoff: str | pd.Timestamp, ids: list[str] | None = None) -> pd.DataFrame:
        self.validate(task)
        t = timestamp(cutoff)
        ids = self.entities(t) if ids is None else ids
        columns: dict = {}
        index = pd.Index(ids, name="entity_id")
        for e in self.schema.events:
            frame = self.snapshot.event_rows(e.table, ids)
            frame = frame.loc[(frame[e.occurred_at] <= t) & (frame[e.available_at] <= t) &
                              (frame[e.occurred_at] > t - pd.Timedelta(days=task.history_days))]
            # Stable tie-breaking makes row reordering irrelevant to the last-value feature.
            frame = frame.sort_values([e.occurred_at, e.available_at, e.key], kind="stable")
            groups = frame.groupby(e.entity_key, sort=False)
            count = groups.size().reindex(ids, fill_value=0).astype(float)
            columns[f"{e.table}__count"] = count
            last = groups[e.occurred_at].max().reindex(ids)
            columns[f"{e.table}__recency"] = (t - last).dt.total_seconds().div(86400).fillna(task.history_days + 1)
            for c in e.values:
                columns[f"{e.table}__{c}__sum"] = (
                    _grouped_sum(frame, e.entity_key, c).map(_finite).astype(float).reindex(ids).fillna(0))
                columns[f"{e.table}__{c}__mean"] = _grouped_mean(frame, e.entity_key, c).reindex(ids, fill_value=0)
                columns[f"{e.table}__{c}__missing"] = count - groups[c].count().reindex(ids, fill_value=0)
                columns[f"{e.table}__{c}__std"] = _grouped_std(frame, e.entity_key, c).reindex(ids, fill_value=0)
                # groupby.last skips nulls; the missing-count feature preserves that limitation.
                latest = groups[c].last().reindex(ids).fillna(0)
                columns[f"{e.table}__{c}__last"] = latest
                columns[f"{e.table}__{c}__trend"] = latest - columns[f"{e.table}__{c}__mean"]
            for days in (1, 7, 30):
                if days >= task.history_days:
                    continue
                recent_frame = frame.loc[frame[e.occurred_at] > t - pd.Timedelta(days=days)]
                recent = recent_frame.groupby(e.entity_key, sort=False)
                columns[f"{e.table}__recent{days}__count"] = recent.size().reindex(ids, fill_value=0)
                for c in e.values:
                    columns[f"{e.table}__recent{days}__{c}__sum"] = (
                        _grouped_sum(recent_frame, e.entity_key, c).map(_finite).astype(float).reindex(ids).fillna(0))
                    columns[f"{e.table}__recent{days}__{c}__mean"] = _grouped_mean(recent_frame, e.entity_key, c).reindex(ids, fill_value=0)
        result = pd.DataFrame(columns, index=index).astype(float)
        if not np.isfinite(result.to_numpy()).all():
            raise ValueError("Derived feature is not representable as finite binary64")
        return result

    def labels(self, task: Task, cutoff: str | pd.Timestamp, as_of: str | pd.Timestamp) -> pd.Series:
        self.validate(task)
        t = timestamp(cutoff)
        end = t + pd.Timedelta(days=task.horizon_days)
        mature = end + pd.Timedelta(days=task.label_delay_days)
        if mature > timestamp(as_of):
            raise ValueError(f"Outcome labels not mature until {mature}")
        e = next(e for e in self.schema.events if e.table == task.outcome.table)
        df = self.snapshot._tables[e.table]
        df = df.loc[(df[e.occurred_at] > t) & (df[e.occurred_at] <= end) & (df[e.available_at] <= mature)]
        if task.outcome.aggregation == "sum" and df[task.outcome.column].isna().any():
            raise ValueError("Missing outcome values in label window; declare eligibility before training")
        group = df.groupby(e.entity_key)
        y: pd.Series
        if task.outcome.aggregation == "count":
            y = group.size()
        else:
            assert task.outcome.column is not None
            y = _grouped_sum(df, e.entity_key, task.outcome.column)
        y = y.reindex(self.entities(t), fill_value=0)
        if task.kind == "binary":
            y = y.gt(task.outcome.threshold)
        return y.map(_finite).astype(float).rename("target")

    def provenance(self, task: Task, cutoff: str, entity_id: str) -> dict:
        t = timestamp(cutoff)
        records = {}
        for e in self.schema.events:
            df = self.snapshot._tables[e.table]
            rows = df.loc[(df[e.entity_key] == entity_id) & (df[e.occurred_at] <= t) &
                          (df[e.available_at] <= t) & (df[e.occurred_at] > t - pd.Timedelta(days=task.history_days))]
            records[e.table] = rows[e.key].tolist()
        return {"entity_id": entity_id, "records": records,
                "meaning": "Input membership for aggregate computation; not causal attribution"}

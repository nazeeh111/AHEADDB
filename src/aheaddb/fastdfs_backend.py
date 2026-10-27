"""Optional released FastDFS + ExtraTrees CPU comparison.

This is a relational aggregate pipeline, not a foundation model. The optional
dependency is imported only when used. It never reads undeclared source columns.
"""
from __future__ import annotations

import importlib.metadata
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor

from .compiler import Compiler
from .modeling import metrics
from .snapshot import Snapshot
from .spec import Task, digest, timestamp

FEATURE_VERSION = "fastdfs-0.2.1:depth2:count,sum,mean,min,max:prefilter-bitemporal-v1"


def features(snapshot: Snapshot, task: Task, cutoff: str) -> pd.DataFrame:
    """Generate DFS features from exactly the records visible at this cutoff.

    Names are normalized before passing the relational database to the optional
    package. Keys, times and unrequested entity attributes cannot become numeric
    predictors. Each record's age is computed relative to the current cutoff.
    """
    # This upstream release checks/unlinks its path even for DuckDB's sentinel.
    # Refuse a colliding real file instead of allowing that dependency side effect.
    if Path(":memory:").exists():
        raise ValueError("A file named ':memory:' exists in this working directory; choose another working directory")
    try:
        import fastdfs
    except ImportError as exc:
        raise RuntimeError("Install the isolated FastDFS environment; see docs/research.md") from exc
    if importlib.metadata.version("fastdfs") != "0.2.1":
        raise RuntimeError("This adapter requires the verified fastdfs==0.2.1 distribution")
    compiler = Compiler(snapshot)
    compiler.validate(task)
    t = timestamp(cutoff)
    ids = compiler.entities(t)
    if not ids:
        raise ValueError("No entities are available at this cutoff")
    schema = snapshot.schema
    tables = {"entities": pd.DataFrame({"entity_id": ids})}
    pks = {"entities": "entity_id"}
    fks = []
    hints = {}
    for i, event in enumerate(schema.events):
        df = snapshot.table(event.table)
        mask = ((df[event.occurred_at] <= t) & (df[event.available_at] <= t)
                & (df[event.occurred_at] > t - pd.Timedelta(days=task.history_days))
                & df[event.entity_key].isin(ids))
        df = df.loc[mask]
        table = f"events_{i}"
        projected = pd.DataFrame({"row_id": df[event.key].astype(str),
                                  "entity_id": df[event.entity_key].astype(str),
                                  "age_days": (t - df[event.occurred_at]).dt.total_seconds() / 86400})
        for j, column in enumerate(event.values):
            projected[f"value_{j}"] = df[column].astype(float)
        tables[table] = projected.reset_index(drop=True)
        pks[table] = "row_id"
        fks.append((table, "entity_id", "entities", "entity_id"))
        hints[table] = {c: "float" for c in projected if c not in {"row_id", "entity_id"}}
    rdb = fastdfs.create_rdb(tables=tables, name="aheaddb_visible_history", primary_keys=pks,
                             foreign_keys=fks, type_hints=hints)
    result = fastdfs.compute_dfs_features(
        rdb=rdb, target_dataframe=pd.DataFrame({"entity_id": ids}),
        key_mappings={"entity_id": "entities.entity_id"},
        config=fastdfs.DFSConfig(engine="dfs2sql", engine_path=":memory:",
                                 max_depth=2, agg_primitives=["count", "sum", "mean", "min", "max"]),
    )
    # Input membership already enforces both clocks and the left-open window.
    result = result.set_index("entity_id").reindex(ids).select_dtypes(include="number")
    result = result.reindex(sorted(result.columns), axis=1).replace([np.inf, -np.inf], np.nan).fillna(0)
    if result.empty or not len(result.columns):
        raise ValueError("FastDFS generated no numeric features for this history")
    result.index.name = "entity_id"
    return result.astype(float)


@dataclass
class FastDFSModel:
    task: Task
    estimator: Any
    columns: list[str]
    schema_version: str
    training_version: str
    trained_as_of: str

    def predict(self, snapshot: Snapshot, cutoff: str) -> np.ndarray:
        if timestamp(cutoff) < timestamp(self.trained_as_of):
            raise ValueError("Prediction cutoff precedes model training time")
        if digest(snapshot.schema.model_dump()) != self.schema_version:
            raise ValueError("Schema differs from FastDFS fitted schema")
        x = features(snapshot, self.task, cutoff)
        if set(x.columns) != set(self.columns):
            raise ValueError("FastDFS feature schema changed; refit rather than silently invent features")
        x = x.reindex(columns=self.columns)
        pred = (self.estimator.predict_proba(x)[:, 1] if self.task.kind == "binary"
                else self.estimator.predict(x))
        if not np.isfinite(pred).all():
            raise ValueError("FastDFS model returned nonfinite predictions")
        return pred


def fit_fastdfs(snapshot: Snapshot, task: Task, cutoffs: list[str], as_of: str) -> FastDFSModel:
    times = sorted(set(map(timestamp, cutoffs)))
    if not times or len(times) != len(cutoffs):
        raise ValueError("Provide distinct FastDFS training cutoffs")
    compiler = Compiler(snapshot)
    xs, ys = [], []
    for cutoff in times:
        # Validate maturity before doing expensive optional feature generation.
        ys.append(compiler.labels(task, cutoff, as_of))
        xs.append(features(snapshot, task, str(cutoff)))
    columns = list(xs[0].columns)
    if any(list(x.columns) != columns for x in xs):
        raise ValueError("FastDFS feature schema differs across training cutoffs")
    x, y = pd.concat(xs), pd.concat(ys)
    if len(y) < 20 or (task.kind == "binary" and y.nunique() < 2):
        raise ValueError("Insufficient history: need 20 mature samples and both binary classes")
    args = {"n_estimators": 80, "min_samples_leaf": 5, "max_features": 1.0,
            "max_depth": 12, "random_state": 17, "n_jobs": 1}
    estimator = (ExtraTreesClassifier(**args) if task.kind == "binary" else ExtraTreesRegressor(**args))
    estimator.fit(x, y)
    return FastDFSModel(task, estimator, columns, digest(snapshot.schema.model_dump()),
                        digest({"data": snapshot.version, "cutoffs": cutoffs, "features": FEATURE_VERSION}),
                        str(timestamp(as_of)))


def backtest_fastdfs(snapshot: Snapshot, task: Task, cutoffs: list[str], as_of: str,
                     min_train_cutoffs: int = 2) -> dict:
    """Match the core chronological folds, maturity rule and fixed estimator size."""
    times = sorted(set(map(timestamp, cutoffs)))
    compiler = Compiler(snapshot)
    folds = []
    for test_time in times:
        train = [str(t) for t in times if t < test_time and
                 t + pd.Timedelta(days=task.horizon_days + task.label_delay_days) <= test_time]
        if len(train) < min_train_cutoffs or test_time + pd.Timedelta(
                days=task.horizon_days + task.label_delay_days) > timestamp(as_of):
            continue
        start = time.perf_counter()
        try:
            model = fit_fastdfs(snapshot, task, train, str(test_time))
            pred = model.predict(snapshot, str(test_time))
            y = compiler.labels(task, test_time, as_of)
            folds.append({"cutoff": str(test_time), "backend": "fastdfs-extratrees", "training_cutoffs": train,
                          "n_test": len(y), "seconds": time.perf_counter() - start,
                          "n_features": len(model.columns), "metrics": metrics(y, pred, task.kind), "error": None})
        except (ValueError, RuntimeError) as exc:
            folds.append({"cutoff": str(test_time), "backend": "fastdfs-extratrees", "error": str(exc)})
    if not folds:
        raise ValueError("No mature chronological FastDFS folds")
    return {"task": task.model_dump(), "data_version": snapshot.version, "folds": folds,
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "fastdfs": importlib.metadata.version("fastdfs"), "feature_version": FEATURE_VERSION},
            "limitations": ["Relational aggregates with ExtraTrees, not a pretrained foundation model",
                            "80 trees versus 80 boosting iterations is a fixed-size CPU comparison, not equal FLOPs",
                            "ExtraTrees uses one worker; upstream DuckDB feature-query threads are not explicitly limited",
                            "Source filtering matches core history; generated aggregate feature sets differ",
                            "Probabilities are raw; no post-hoc calibration fitted on held-out rows"]}

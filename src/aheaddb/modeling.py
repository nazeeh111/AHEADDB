"""Executed CPU models with purged chronological evaluation."""
from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    mean_squared_error,
    roc_auc_score,
)
from threadpoolctl import threadpool_limits

from .compiler import Compiler
from .snapshot import Snapshot
from .spec import Task, digest, timestamp


def metrics(y: pd.Series | np.ndarray, pred: np.ndarray, kind: str) -> dict:
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    if len(y) == 0 or not np.isfinite(y).all() or not np.isfinite(pred).all():
        raise ValueError("Cannot evaluate empty or nonfinite results")
    if kind == "regression":
        return {"mae": float(mean_absolute_error(y, pred)), "rmse": float(np.sqrt(mean_squared_error(y, pred)))}
    two = len(np.unique(y)) == 2
    bins = []
    for left in np.linspace(0, 0.8, 5):
        mask = (pred >= left) & ((pred < left + 0.2) if left < 0.8 else (pred <= 1))
        if mask.any():
            bins.append({"lower": float(left), "n": int(mask.sum()), "probability": float(pred[mask].mean()),
                         "frequency": float(y[mask].mean())})
    return {"roc_auc": float(roc_auc_score(y, pred)) if two else None,
            "average_precision": float(average_precision_score(y, pred)) if two else None,
            "brier": float(brier_score_loss(y, pred)),
            "ece_5_bins": float(sum(b["n"] * abs(b["probability"] - b["frequency"]) for b in bins) / len(y)),
            "reliability": bins, "prevalence": float(y.mean()),
            "limitation": None if two else "Only one outcome class; ranking metrics undefined"}


@dataclass
class FittedModel:
    task: Task
    estimator: Any
    columns: list[str]
    trained_as_of: str
    training_version: str
    backend: str
    schema_version: str
    training_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def version(self) -> str:
        # Recompute to detect accidental mutation of model/preprocessing in a long-lived process.
        return digest({"weights": joblib.hash(self.estimator, hash_name="sha1"), "task": self.task.version,
                       "columns": self.columns, "as_of": self.trained_as_of, "training": self.training_version,
                       "backend": self.backend, "preprocessing": Compiler.PREPROCESSING,
                       "sklearn": sklearn.__version__, "schema": self.schema_version,
                       "training_ranges": self.training_ranges})

    def diagnostics(self, features: pd.DataFrame) -> dict:
        outside = pd.Series(False, index=features.index)
        for column, (low, high) in self.training_ranges.items():
            outside |= (features[column] < low) | (features[column] > high)
        counts = [c for c in features if str(c).endswith("__count")]
        no_history = features[counts].sum(axis=1).eq(0) if counts else pd.Series(True, index=features.index)
        return {"rows_outside_training_range": int(outside.sum()), "rows_without_history": int(no_history.sum()),
                "range_check": "Simple feature min/max screen, not a statistical shift test",
                "calibration": "Raw model probabilities; inspect backtest reliability before using"}

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        if list(features.columns) != self.columns:
            raise ValueError("Feature schema differs from fitted preprocessing")
        if len(features) == 0:
            return np.array([], dtype=float)
        with threadpool_limits(limits=1):
            if self.task.kind == "binary":
                result = self.estimator.predict_proba(features)[:, 1]
            else:
                result = self.estimator.predict(features)
        if not np.isfinite(result).all():
            raise ValueError("Model returned nonfinite predictions")
        return result


def fit(snapshot: Snapshot, task: Task, cutoffs: list[str], as_of: str,
        backend: str = "histogram") -> FittedModel:
    if not cutoffs or len(set(map(str, map(timestamp, cutoffs)))) != len(cutoffs):
        raise ValueError("Provide distinct training cutoffs")
    compiler = Compiler(snapshot)
    xs, ys = [], []
    for t in sorted(cutoffs, key=timestamp):
        x = compiler.features(task, t)
        y = compiler.labels(task, t, as_of)
        xs.append(x)
        ys.append(y)
    x, y = pd.concat(xs), pd.concat(ys)
    if len(y) < 20 or (task.kind == "binary" and y.nunique() < 2):
        raise ValueError("Insufficient history: need 20 mature samples and both binary classes")
    if backend == "baseline":
        estimator = DummyClassifier(strategy="prior") if task.kind == "binary" else DummyRegressor(strategy="mean")
    elif backend == "histogram":
        args = {"max_iter": 80, "max_leaf_nodes": 15, "min_samples_leaf": 10,
                "l2_regularization": 2, "early_stopping": False, "random_state": 17}
        estimator = (HistGradientBoostingClassifier(**args) if task.kind == "binary" else
                     HistGradientBoostingRegressor(**args))
    else:
        raise ValueError("Unknown backend; choose baseline or histogram")
    with threadpool_limits(limits=1):
        estimator.fit(x, y)
    return FittedModel(task, estimator, list(x.columns), str(timestamp(as_of)),
                       digest({"data": snapshot.version, "cutoffs": cutoffs}), backend,
                       digest(snapshot.schema.model_dump()),
                       {str(c): (float(x[c].min()), float(x[c].max())) for c in x})


def backtest(snapshot: Snapshot, task: Task, cutoffs: list[str], as_of: str,
             backends: tuple[str, ...] = ("baseline", "histogram"), min_train_cutoffs: int = 2) -> dict:
    """Every fold fits preprocessing/model only on labels mature at validation time."""
    times = sorted(set(map(timestamp, cutoffs)))
    compiler = Compiler(snapshot)
    folds = []
    for test_time in times:
        train = [str(t) for t in times if t < test_time and
                 t + pd.Timedelta(days=task.horizon_days + task.label_delay_days) <= test_time]
        if len(train) < min_train_cutoffs or test_time + pd.Timedelta(
                days=task.horizon_days + task.label_delay_days) > timestamp(as_of):
            continue
        for backend in backends:
            start = time.perf_counter()
            try:
                model = fit(snapshot, task, train, str(test_time), backend)
                pred = model.predict(compiler.features(task, test_time))
                y = compiler.labels(task, test_time, as_of)
                folds.append({"cutoff": str(test_time), "backend": backend, "training_cutoffs": train,
                              "n_test": len(y), "seconds": time.perf_counter() - start,
                              "metrics": metrics(y, pred, task.kind), "error": None})
            except ValueError as exc:
                folds.append({"cutoff": str(test_time), "backend": backend, "error": str(exc)})
    if not folds:
        raise ValueError("No mature chronological folds; extend history or shorten horizon")
    return {"task": task.model_dump(), "data_version": snapshot.version, "folds": folds,
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "sklearn": sklearn.__version__},
            "limitations": ["Entities recur across time; folds are not independent IID samples",
                            "Probabilities are raw model outputs; calibration is measured, not guaranteed",
                            "No tuning on held-out folds; fixed CPU budgets"]}

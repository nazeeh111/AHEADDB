"""Executed CPU models with purged chronological evaluation."""
from __future__ import annotations

import hashlib
import pickle
import platform
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    mean_squared_error,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
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
    selection: dict = field(default_factory=dict)

    @property
    def version(self) -> str:
        # Recompute to detect accidental mutation of model/preprocessing in a long-lived process.
        # C-backed serialization detects arbitrary estimator mutation without joblib's
        # Python-level traversal overhead. Bytes stay in memory; nothing is loaded.
        return digest({"weights": hashlib.sha256(pickle.dumps(self.estimator, protocol=5)).hexdigest(), "task": self.task.version,
                       "columns": self.columns, "as_of": self.trained_as_of, "training": self.training_version,
                       "backend": self.backend, "preprocessing": Compiler.PREPROCESSING,
                       "sklearn": sklearn.__version__, "schema": self.schema_version,
                       "training_ranges": self.training_ranges, "selection": self.selection})

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


def signed_log(x):
    return np.sign(x) * np.log1p(np.abs(x))


class PersistenceEstimator:
    """History rate for counts; latest observed value times rate for sums.

    Binary probabilities use a one-dimensional logistic fit on training outcomes.
    This is a selectable conventional model, not a domain-specific forecast rule.
    """
    def __init__(self, task: Task):
        self.task = task
        self.calibrator: Any = None

    def _projection(self, x: pd.DataFrame) -> np.ndarray:
        rate = x[f"{self.task.outcome.table}__count"].to_numpy(dtype=float) * self.task.horizon_days / self.task.history_days
        if self.task.outcome.aggregation == "sum":
            return rate * x[f"{self.task.outcome.table}__{self.task.outcome.column}__last"].to_numpy(dtype=float)
        return rate

    def fit(self, x, y):
        if self.task.kind == "binary":
            self.calibrator = make_pipeline(StandardScaler(), LogisticRegression(C=1, max_iter=500, random_state=17))
            self.calibrator.fit(signed_log(self._projection(x)).reshape(-1, 1), y)
        return self

    def predict_proba(self, x):
        return self.calibrator.predict_proba(signed_log(self._projection(x)).reshape(-1, 1))

    def predict(self, x):
        return self._projection(x) if self.task.kind == "regression" else self.predict_proba(x)[:, 1] >= 0.5


def _estimator(task: Task, backend: str):
    if backend == "persistence":
        return PersistenceEstimator(task)
    if backend == "baseline":
        return DummyClassifier(strategy="prior") if task.kind == "binary" else DummyRegressor(strategy="mean")
    if backend == "median" and task.kind == "regression":
        return DummyRegressor(strategy="median")
    if backend == "linear" and task.kind == "binary":
        return make_pipeline(FunctionTransformer(signed_log, feature_names_out="one-to-one"),
                             StandardScaler(), LogisticRegression(C=0.1, max_iter=500, random_state=17))
    if backend in {"histogram", "robust"}:
        robust = backend == "robust"
        args = {"max_iter": 80, "max_leaf_nodes": 7 if robust else 15, "min_samples_leaf": 20 if robust else 10,
                "l2_regularization": 10 if robust else 2, "early_stopping": False, "random_state": 17}
        if task.kind == "binary":
            return HistGradientBoostingClassifier(**args)
        return HistGradientBoostingRegressor(loss="absolute_error" if robust and task.metric == "mae" else "squared_error", **args)
    raise ValueError("Unsupported backend/task combination; choose baseline, histogram, robust, adaptive, persistence, binary linear or regression median")


def _select(task: Task, xs: list[pd.DataFrame], ys: list[pd.Series], times: list[pd.Timestamp]) -> tuple[str, dict]:
    if len(times) < 2:
        return "baseline", {"method": "purged-pooled-block-v2", "selected": "baseline", "metric": task.metric,
                            "validation_scores": {}, "folds": [], "validation_cutoffs": [],
                            "limitation": "Insufficient mature inner validation; prior/mean fallback"}
    candidates = ("baseline", "histogram", "robust", "linear", "persistence") if task.kind == "binary" else (
        "baseline", "median", "histogram", "robust", "persistence")
    records = []
    scores: dict[str, float] = {}
    first = max(1, len(times) - min(12, max(2, len(times) // 5)))
    train = [j for j in range(first) if times[j] + pd.Timedelta(
        days=task.horizon_days + task.label_delay_days) <= times[first]]
    validation = list(range(first, len(times)))
    if train:
        x, y = pd.concat([xs[j] for j in train]), pd.concat([ys[j] for j in train])
        valid_y = pd.concat([ys[i] for i in validation])
    else:
        x, y, valid_y = pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float)
    usable = len(y) >= 20 and len(valid_y) > 0 and (task.kind != "binary" or (y.nunique() == 2 and valid_y.nunique() == 2))
    if usable:
        for candidate in candidates:
            estimator = _estimator(task, candidate)
            with threadpool_limits(limits=1):
                estimator.fit(x, y)
                predictions = []
                for i in validation:
                    if len(xs[i]) == 0:
                        continue
                    pred = estimator.predict_proba(xs[i])[:, 1] if task.kind == "binary" else estimator.predict(xs[i])
                    predictions.append(pred)
                    records.append({"candidate": candidate, "validation_cutoff": str(times[i]),
                                    "training_cutoffs": [str(times[j]) for j in train], "samples": len(ys[i]),
                                    "metric": task.metric, "score": metrics(ys[i], pred, task.kind)[task.metric]})
            scores[candidate] = float(metrics(valid_y, np.concatenate(predictions), task.kind)[task.metric])
    chosen = (min(scores, key=lambda n: scores[n]) if task.kind == "regression" else
              max(scores, key=lambda n: scores[n])) if scores else "baseline"
    return chosen, {"method": "purged-pooled-block-v2", "selected": chosen,
                    "metric": task.metric, "validation_scores": scores, "folds": records,
                    "validation_cutoffs": [str(times[i]) for i in validation],
                    "limitation": None if scores else "Insufficient mature inner validation; prior/mean fallback"}


def fit(snapshot: Snapshot, task: Task, cutoffs: list[str], as_of: str,
        backend: str = "adaptive") -> FittedModel:
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
    selected, selection = (_select(task, xs, ys, sorted(map(timestamp, cutoffs))) if backend == "adaptive" else
                           (backend, {"method": "explicit", "selected": backend}))
    estimator = _estimator(task, selected)
    with threadpool_limits(limits=1):
        estimator.fit(x, y)
    return FittedModel(task, estimator, list(x.columns), str(timestamp(as_of)),
                       digest({"data": snapshot.version, "cutoffs": cutoffs}), backend,
                       digest(snapshot.schema.model_dump()),
                       {str(c): (float(x[c].min()), float(x[c].max())) for c in x}, selection)


def backtest(snapshot: Snapshot, task: Task, cutoffs: list[str], as_of: str,
             backends: tuple[str, ...] = ("baseline", "histogram", "adaptive"), min_train_cutoffs: int = 2) -> dict:
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
                              "metrics": metrics(y, pred, task.kind), "selection": model.selection, "error": None})
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

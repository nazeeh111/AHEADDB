"""Reproduce fixed-budget chronological prediction experiments on two public datasets.

Run fetch_public_data.py first. This is deliberately separate from routine tests.
No downloaded data or fitted model is included in the source release.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from aheaddb import Compiler, Outcome, Schema, Snapshot, Task, fit
from aheaddb.modeling import metrics
from aheaddb.spec import timestamp

ROOT = Path(__file__).resolve().parents[1]


def valid_beijing(snapshot: Snapshot, cutoffs: list[str]) -> tuple[list[str], int]:
    """Missing pollution readings are unknown labels, never zero pollution."""
    df = snapshot.table("readings")
    valid = []
    n_entities = len(snapshot.table("entities"))
    for c in cutoffs:
        start = timestamp(c)
        end = start + pd.Timedelta(days=1)
        target = df.loc[(df.occurred_at > start) & (df.occurred_at <= end) & (df.available_at <= end)]
        if len(target) == n_entities and target.pm25.notna().all():
            valid.append(c)
    return valid, len(cutoffs) - len(valid)


def dataset_setup(dataset: str, data_dir: Path = ROOT / ".data") -> tuple[Snapshot, list[Task], list[dict], dict]:
    schema = Schema.model_validate_json((data_dir / f"{dataset}-schema.json").read_text())
    snapshot = Snapshot.duckdb(data_dir / f"{dataset}.duckdb", schema)
    provenance = json.loads((data_dir / f"{dataset}-provenance.json").read_text())
    if dataset == "retail":
        tasks = [
            Task(name="purchase_30d", kind="binary", outcome=Outcome(table="purchases"),
                 horizon_days=30, history_days=90, label_delay_days=1, metric="roc_auc"),
            Task(name="gross_spend_30d", kind="regression", outcome=Outcome(table="purchases", aggregation="sum", column="spend"),
                 horizon_days=30, history_days=90, label_delay_days=1, metric="mae"),
        ]
        initial = ["2011-03-01", "2011-03-15", "2011-04-01", "2011-04-15", "2011-05-01", "2011-05-15", "2011-06-01"]
        folds = [
            {"name": "summer", "train": initial, "fit_as_of": "2011-07-15", "test": ["2011-08-01", "2011-09-01"]},
            {"name": "autumn", "train": initial + ["2011-06-15", "2011-07-01", "2011-07-15", "2011-08-01"],
             "fit_as_of": "2011-09-15", "test": ["2011-10-01", "2011-11-01"]},
        ]
    elif dataset == "beijing":
        tasks = [
            Task(name="pm25_over_75_next_day", kind="binary", outcome=Outcome(table="readings", aggregation="sum", column="pm25", threshold=75),
                 horizon_days=1, history_days=7, metric="roc_auc"),
            Task(name="pm25_next_day", kind="regression", outcome=Outcome(table="readings", aggregation="sum", column="pm25"),
                 horizon_days=1, history_days=7, metric="mae"),
        ]
        folds = []
        for year in [2015, 2016]:
            train = pd.date_range("2013-05-01", f"{year-1}-12-29", freq="7D").strftime("%Y-%m-%d").tolist()
            test = pd.date_range(f"{year}-03-01", f"{year}-06-30", freq="7D").strftime("%Y-%m-%d").tolist()
            train, skipped_train = valid_beijing(snapshot, train)
            test, skipped_test = valid_beijing(snapshot, test)
            folds.append({"name": str(year), "train": train, "fit_as_of": f"{year}-01-01", "test": test,
                          "skipped_missing_train_cutoffs": skipped_train, "skipped_missing_test_cutoffs": skipped_test})
    else:
        raise ValueError("Unknown dataset")
    for task in tasks:
        for fold in folds:
            assert all(timestamp(c) + pd.Timedelta(days=task.horizon_days + task.label_delay_days) <= timestamp(fold["fit_as_of"])
                       for c in fold["train"])
            assert all(timestamp(c) >= timestamp(fold["fit_as_of"]) for c in fold["test"])
    return snapshot, tasks, folds, provenance


def historical(snapshot: Snapshot, task: Task, cutoff: str) -> np.ndarray:
    compiler = Compiler(snapshot)
    x = compiler.features(task, cutoff)
    if task.outcome.table == "purchases":
        if task.kind == "binary":
            return 1 - np.exp(-x.purchases__count.to_numpy() * task.horizon_days / task.history_days)
        return x.purchases__spend__sum.to_numpy() * task.horizon_days / task.history_days
    if task.kind == "regression":
        return x.readings__pm25__mean.to_numpy()
    df = snapshot.table("readings")
    t = timestamp(cutoff)
    df = df.loc[(df.occurred_at > t - pd.Timedelta(days=task.history_days)) &
                (df.occurred_at <= t) & (df.available_at <= t) & df.pm25.notna()]
    p = df.assign(exceed=df.pm25 > 75).groupby("entity_id").exceed.mean()
    return p.reindex(x.index).fillna(0.5).to_numpy()


def rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


def evaluate(dataset: str, dest: Path, data_dir: Path = ROOT / ".data") -> dict:
    started = time.perf_counter()
    snapshot, tasks, folds, provenance = dataset_setup(dataset, data_dir)
    compiler = Compiler(snapshot)
    rows, summaries = [], []
    for task in tasks:
        task_runs = {name: {"y": [], "p": [], "folds": [], "seconds": 0.0} for name in ["historical", "baseline", "histogram"]}
        for fold in folds:
            for backend in task_runs:
                start = time.perf_counter()
                model = None if backend == "historical" else fit(snapshot, task, fold["train"], fold["fit_as_of"], backend)
                fit_seconds = time.perf_counter() - start
                fold_y, fold_p = [], []
                predict_start = time.perf_counter()
                for cutoff in fold["test"]:
                    x = compiler.features(task, cutoff)
                    y = compiler.labels(task, cutoff, "2012-01-01" if dataset == "retail" else "2017-03-02")
                    pred = historical(snapshot, task, cutoff) if model is None else model.predict(x)
                    fold_y.extend(y.tolist())
                    fold_p.extend(pred.tolist())
                    for entity, true, prediction in zip(x.index, y, pred, strict=True):
                        rows.append({"dataset": dataset, "task": task.name, "fold": fold["name"], "backend": backend,
                                     "cutoff": cutoff, "entity_id": entity, "actual": float(true), "prediction": float(prediction)})
                predict_seconds = time.perf_counter() - predict_start
                run = task_runs[backend]
                run["y"].extend(fold_y)
                run["p"].extend(fold_p)
                run["seconds"] += fit_seconds + predict_seconds
                run["folds"].append({"name": fold["name"], "train_cutoffs": fold["train"], "fit_as_of": fold["fit_as_of"],
                                     "test_cutoffs": fold["test"], "train_samples": sum(len(compiler.entities(c)) for c in fold["train"]),
                                     "test_samples": len(fold_y), "fit_seconds": fit_seconds, "predict_seconds": predict_seconds,
                                     "process_peak_rss_bytes": rss_bytes(), "metrics": metrics(np.array(fold_y), np.array(fold_p), task.kind),
                                     "model_version": model.version if model else None})
                print(f"{dataset}/{task.name}/{fold['name']}/{backend}: {len(fold_y)} test samples", flush=True)
        for backend, run in task_runs.items():
            summaries.append({"task": task.model_dump(), "backend": backend, "test_samples": len(run["y"]),
                              "seconds": run["seconds"], "metrics": metrics(np.array(run["y"]), np.array(run["p"]), task.kind),
                              "folds": run["folds"]})
    report = {
        "dataset": dataset, "source": provenance, "data_version": snapshot.version, "schema": snapshot.schema.model_dump(),
        "results": summaries, "folds": folds, "total_seconds": time.perf_counter() - started,
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(),
                        "sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__,
                        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
                        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS")},
        "limitations": [
            "These are actual public-data predictions, separate from synthetic refresh stress tests.",
            "Availability timestamps and source timezone are assumptions; source does not record when information became available.",
            "Entity IDs recur across temporal samples. Samples and folds are dependent; no IID confidence intervals are claimed.",
            "Fixed hyperparameters, no test tuning, no pretrained foundation model or zero-shot transfer claim.",
            "Baseline is training-prior/mean; historical is a separate task-specific rate/persistence baseline.",
            "Binary probabilities are uncalibrated raw estimates; reliability/Brier/ECE expose calibration error.",
            "Peak RSS is cumulative process high-water memory including loaded source tables, not per-model allocation.",
            "Retail task excludes refunds/cancellations and forecasts positive gross spend, not business net revenue.",
            "Beijing retains only cutoffs with complete next-day labels across all stations; this can bias results toward better observed days.",
            "Beijing is normalized from station time series; this is not an original multi-hop relational benchmark.",
        ],
    }
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"public-{dataset}.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    pd.DataFrame(rows).to_csv(dest / f"public-{dataset}-predictions.csv", index=False)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["retail", "beijing", "all"], default="all")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".data")
    parser.add_argument("--dest", type=Path, default=ROOT / "artifacts" / "results")
    args = parser.parse_args()
    for dataset in ["retail", "beijing"] if args.dataset == "all" else [args.dataset]:
        evaluate(dataset, args.dest, args.data_dir)

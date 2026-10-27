"""Run the optional released relational pipeline on the exact core temporal folds.

PYTHONPATH=src .runtime/fastdfs/bin/python scripts/evaluate_fastdfs.py \
  --database path.duckdb --schema schema.json --task task.json \
  --cutoffs 2020-03-01,2020-04-01,2020-05-01 --as-of 2020-07-01 --output results/fastdfs.json
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from aheaddb import Compiler, Schema, Snapshot, Task
from aheaddb.fastdfs_backend import backtest_fastdfs, fit_fastdfs
from aheaddb.modeling import metrics

ROOT = Path(__file__).resolve().parents[1]


def execution_policy() -> dict:
    return {"estimator_workers": 1, "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
            "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
            "VECLIB_MAXIMUM_THREADS": os.environ.get("VECLIB_MAXIMUM_THREADS"),
            "duckdb_feature_query_threads": "upstream default, not explicitly limited"}


def evaluate_public(dataset: str, dest: Path, data_dir: Path) -> dict:
    # The same setup owns source handling, missing-label exclusions and fold dates.
    from evaluate_public import dataset_setup
    snapshot, tasks, folds, provenance = dataset_setup(dataset, data_dir)
    compiler = Compiler(snapshot)
    summaries, predictions = [], []
    for task in tasks:
        all_y, all_p, fold_reports = [], [], []
        for fold in folds:
            started = time.perf_counter()
            model = fit_fastdfs(snapshot, task, fold["train"], fold["fit_as_of"])
            fit_seconds = time.perf_counter() - started
            predict_start = time.perf_counter()
            fold_y, fold_p = [], []
            for cutoff in fold["test"]:
                y = compiler.labels(task, cutoff, "2012-01-01" if dataset == "retail" else "2017-03-02")
                pred = model.predict(snapshot, cutoff)
                fold_y.extend(y.tolist())
                fold_p.extend(pred.tolist())
                for entity, true, prediction in zip(compiler.entities(cutoff), y, pred, strict=True):
                    predictions.append({"dataset": dataset, "task": task.name, "fold": fold["name"],
                                        "backend": "fastdfs-extratrees", "cutoff": cutoff, "entity_id": entity,
                                        "actual": float(true), "prediction": float(prediction)})
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            fold_reports.append({"name": fold["name"], "train_cutoffs": fold["train"],
                                 "fit_as_of": fold["fit_as_of"], "test_cutoffs": fold["test"],
                                 "train_samples": sum(len(compiler.entities(c)) for c in fold["train"]),
                                 "test_samples": len(fold_y), "n_features": len(model.columns),
                                 "fit_seconds": fit_seconds, "predict_seconds": time.perf_counter() - predict_start,
                                 "process_peak_rss_bytes": int(rss if sys.platform == "darwin" else rss * 1024),
                                 "metrics": metrics(np.array(fold_y), np.array(fold_p), task.kind)})
            all_y.extend(fold_y)
            all_p.extend(fold_p)
            print(f"{dataset}/{task.name}/{fold['name']}/fastdfs-extratrees: {len(fold_y)} samples", flush=True)
        summaries.append({"task": task.model_dump(), "backend": "fastdfs-extratrees", "test_samples": len(all_y),
                          "metrics": metrics(np.array(all_y), np.array(all_p), task.kind), "folds": fold_reports})
    report = {"dataset": dataset, "source": provenance, "data_version": snapshot.version, "results": summaries,
              "execution_policy": execution_policy(),
              "folds": folds, "dependencies": {name: importlib.metadata.version(name) for name in
              ["fastdfs", "featuretools", "woodwork", "setuptools", "duckdb", "pandas", "numpy", "scikit-learn",
               "pyarrow", "sqlglot"]}, "limitations": [
                  "Released FastDFS plus ExtraTrees is relational aggregate modeling, not full RDBLearn or an RFM",
                  "Exact core source snapshot, train/test rows and cutoffs; aggregate feature sets differ",
                  "80 trees versus 80 boosting iterations is fixed-size CPU comparison, not equal FLOPs",
                  "ExtraTrees n_jobs=1; upstream DuckDB feature-query threads are not explicitly limited",
                  "Source availability and missing-label assumptions match public core evaluation",
                  "Peak RSS is a cumulative process high-water mark including imports and source tables",
              ]}
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"fastdfs-{dataset}.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    pd.DataFrame(predictions).to_csv(dest / f"fastdfs-{dataset}-predictions.csv", index=False)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", choices=["retail", "beijing", "all"])
    p.add_argument("--dest", type=Path, default=ROOT / "artifacts" / "results")
    p.add_argument("--data-dir", type=Path, default=ROOT / ".data")
    p.add_argument("--database", type=Path)
    p.add_argument("--schema", type=Path)
    p.add_argument("--task", type=Path)
    p.add_argument("--cutoffs")
    p.add_argument("--as-of")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if args.dataset:
        for dataset in ["retail", "beijing"] if args.dataset == "all" else [args.dataset]:
            evaluate_public(dataset, args.dest, args.data_dir)
        return
    if not all([args.database, args.schema, args.task, args.cutoffs, args.as_of, args.output]):
        p.error("Use --dataset, or provide all of --database --schema --task --cutoffs --as-of --output")
    schema = Schema.model_validate_json(args.schema.read_text())
    task = Task.model_validate_json(args.task.read_text())
    snapshot = Snapshot.duckdb(args.database, schema)
    report = backtest_fastdfs(snapshot, task, args.cutoffs.split(","), args.as_of)
    report["execution_policy"] = execution_policy()
    report["dependencies"] = {name: importlib.metadata.version(name) for name in
                              ["fastdfs", "featuretools", "woodwork", "setuptools", "duckdb", "pandas",
                               "numpy", "scikit-learn", "pyarrow", "sqlglot"]}
    report["process_peak_rss_native_units"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["memory_note"] = "Process peak includes imports and source data; bytes on macOS, KiB on Linux"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()

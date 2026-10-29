"""Frozen temporal comparison of executed v0.1.0 models and the adaptive backend.

First run --freeze-only, before evaluating final periods. See the protocol document.
Raw sources and fitted models are never exported by this script.
"""
from __future__ import annotations

# ruff: noqa: E402
import argparse
import hashlib
import importlib.util
import io
import json
import os
import platform
import resource
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Set before numerical-library imports; threadpoolctl also constrains loaded pools.
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"

import numpy as np
import pandas as pd
import sklearn
from evaluate_public import dataset_setup, valid_beijing
from threadpoolctl import threadpool_limits

from aheaddb import Compiler, Task, fit
from aheaddb.modeling import metrics
from aheaddb.spec import timestamp

ROOT = Path(__file__).resolve().parents[1]
LEGACY_COMMIT = "7c84f7aaa0c573340837667ae63a6c9c7b71331a"
PROTOCOL_DOCUMENT = ROOT / "docs" / "v020-evaluation-protocol.md"
BACKENDS = ("legacy_prior", "legacy_historical", "legacy_histogram", "adaptive")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def frozen_protocol(dataset: str, snapshot, published: list[dict]) -> list[dict]:
    """Declare final periods without looking at model predictions or scores."""
    folds = [{**fold, "evidence": "previously_exposed_retrospective"} for fold in published]
    if dataset == "beijing":
        train = pd.date_range("2013-05-01", "2016-07-29", freq="7D").strftime("%Y-%m-%d").tolist()
        test = pd.date_range("2016-09-01", "2017-01-26", freq="7D").strftime("%Y-%m-%d").tolist()
        retained_train, skipped_train = valid_beijing(snapshot, train)
        retained_test, skipped_test = valid_beijing(snapshot, test)
        folds.append({"name": "later_2016_2017", "evidence": "later_fixed_period",
                      "train": retained_train, "fit_as_of": "2016-08-01", "test": retained_test,
                      "proposed_train": train, "proposed_test": test,
                      "skipped_missing_train_cutoffs": skipped_train,
                      "skipped_missing_test_cutoffs": skipped_test})
    else:
        train = published[0]["train"] + ["2011-06-15", "2011-07-01", "2011-07-15", "2011-08-01", "2011-08-15"]
        folds.append({"name": "later_overlapping_november", "evidence": "later_overlapping_exposed_outcomes",
                      "train": train, "fit_as_of": "2011-10-01", "test": ["2011-11-08"]})
    return folds


def freeze(dest: Path, data_dir: Path) -> dict:
    target = dest / "v020-quality-protocol.json"
    if target.exists():
        raise FileExistsError(f"Frozen protocol already exists: {target}; preserve it")
    datasets = {}
    for dataset in ("retail", "beijing"):
        snapshot, tasks, published, provenance = dataset_setup(dataset, data_dir)
        folds = frozen_protocol(dataset, snapshot, published)
        for task in tasks:
            for fold in folds:
                assert all(timestamp(c) + pd.Timedelta(days=task.horizon_days + task.label_delay_days)
                           <= timestamp(fold["fit_as_of"]) for c in fold["train"])
                assert all(timestamp(c) >= timestamp(fold["fit_as_of"]) for c in fold["test"])
        datasets[dataset] = {"data_version": snapshot.version, "schema": snapshot.schema.model_dump(),
                             "source": provenance, "tasks": [task.model_dump() for task in tasks], "folds": folds}
    document = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "legacy_commit": LEGACY_COMMIT,
                "document_sha256": sha256(PROTOCOL_DOCUMENT), "evaluator_sha256": sha256(Path(__file__)),
                "backends": BACKENDS, "datasets": datasets}
    document["protocol_sha256"] = canonical_hash(document)
    dest.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
    print(f"Frozen {target} SHA256 {document['protocol_sha256']}", flush=True)
    return document


def load_legacy(temp: Path):
    """Import original package bytes, including its original feature compiler."""
    actual = subprocess.check_output(["git", "rev-parse", f"{LEGACY_COMMIT}^{{commit}}"], cwd=ROOT, text=True).strip()
    if actual != LEGACY_COMMIT:
        raise ValueError(f"Legacy commit differs: {actual}")
    archive = subprocess.check_output(["git", "archive", LEGACY_COMMIT, "src/aheaddb"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(temp, filter="data")
    package = temp / "src" / "aheaddb"
    spec = importlib.util.spec_from_file_location("aheaddb_v010", package / "__init__.py",
                                                submodule_search_locations=[str(package)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["aheaddb_v010"] = module
    spec.loader.exec_module(module)
    return module


def historical(snapshot, task, cutoff: str, features: pd.DataFrame) -> np.ndarray:
    if task.outcome.table == "purchases":
        if task.kind == "binary":
            return 1 - np.exp(-features.purchases__count.to_numpy() * task.horizon_days / task.history_days)
        return features.purchases__spend__sum.to_numpy() * task.horizon_days / task.history_days
    if task.kind == "regression":
        return features.readings__pm25__mean.to_numpy()
    frame = snapshot.table("readings")
    t = timestamp(cutoff)
    frame = frame.loc[(frame.occurred_at > t - pd.Timedelta(days=task.history_days)) &
                      (frame.occurred_at <= t) & (frame.available_at <= t) & frame.pm25.notna()]
    values = frame.assign(exceed=frame.pm25 > 75).groupby("entity_id").exceed.mean()
    return values.reindex(features.index).fillna(0.5).to_numpy()


def peak_rss() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def extended_metrics(y, pred, kind: str) -> dict:
    result = metrics(np.asarray(y), np.asarray(pred), kind)
    result["minimum_prediction"] = float(np.min(pred))
    result["maximum_prediction"] = float(np.max(pred))
    if kind == "regression":
        result["negative_predictions"] = int((np.asarray(pred) < 0).sum())
        result["negative_prediction_fraction"] = float((np.asarray(pred) < 0).mean())
    return result


def evaluate(dataset: str, protocol: dict, dest: Path, data_dir: Path, legacy) -> dict:
    started = time.perf_counter()
    loading = time.perf_counter()
    snapshot, _, _, _ = dataset_setup(dataset, data_dir)
    config = protocol["datasets"][dataset]
    if snapshot.version != config["data_version"]:
        raise ValueError("Source snapshot differs from frozen protocol")
    old_schema = legacy.Schema.model_validate(config["schema"])
    old_snapshot = legacy.Snapshot({name: snapshot.table(name) for name in
                                   [old_schema.entity_table, *(e.table for e in old_schema.events)]}, old_schema)
    assert old_snapshot.version == snapshot.version
    loading_seconds = time.perf_counter() - loading
    old_compiler, new_compiler = legacy.Compiler(old_snapshot), Compiler(snapshot)
    rows, results = [], []
    for task_config in config["tasks"]:
        task, old_task = Task.model_validate(task_config), legacy.Task.model_validate(task_config)
        for fold in config["folds"]:
            expected_rows, cached = [], []
            feature_seconds = {"legacy": 0.0, "adaptive": 0.0}
            for cutoff in fold["test"]:
                label_as_of = "2012-01-01" if dataset == "retail" else "2017-03-02"
                feature_started = time.perf_counter()
                old_x = old_compiler.features(old_task, cutoff)
                feature_seconds["legacy"] += time.perf_counter() - feature_started
                feature_started = time.perf_counter()
                new_x = new_compiler.features(task, cutoff)
                feature_seconds["adaptive"] += time.perf_counter() - feature_started
                old_y = old_compiler.labels(old_task, cutoff, label_as_of)
                new_y = new_compiler.labels(task, cutoff, label_as_of)
                pd.testing.assert_index_equal(old_x.index, new_x.index)
                pd.testing.assert_series_equal(old_y, new_y)
                pd.testing.assert_index_equal(new_x.index, new_y.index)
                expected_rows.extend([[cutoff, str(entity), float(label)]
                                      for entity, label in zip(new_y.index, new_y, strict=True)])
                cached.append((cutoff, old_x, new_x, new_y))
            row_hash = canonical_hash(expected_rows)
            for backend in BACKENDS:
                result = {"task": task_config, "backend": backend, "fold": fold["name"],
                          "evidence": fold["evidence"], "train_cutoffs": fold["train"],
                          "fit_as_of": fold["fit_as_of"], "test_cutoffs": fold["test"],
                          "test_row_label_sha256": row_hash, "test_samples": len(expected_rows),
                          "same_entity_ids_and_labels_asserted": True,
                          "test_feature_compile_seconds": feature_seconds["adaptive" if backend == "adaptive" else "legacy"]}
                try:
                    fit_started = time.perf_counter()
                    if backend == "legacy_historical":
                        model = None
                    elif backend == "adaptive":
                        model = fit(snapshot, task, fold["train"], fold["fit_as_of"], backend="adaptive")
                    else:
                        model = legacy.fit(old_snapshot, old_task, fold["train"], fold["fit_as_of"],
                                           backend="baseline" if backend == "legacy_prior" else "histogram")
                    result["fit_seconds"] = time.perf_counter() - fit_started
                    y_all, p_all = [], []
                    predict_started = time.perf_counter()
                    for cutoff, old_x, new_x, y in cached:
                        x = new_x if backend == "adaptive" else old_x
                        pred = historical(old_snapshot, old_task, cutoff, old_x) if model is None else model.predict(x)
                        assert len(pred) == len(y)
                        y_all.extend(y.tolist())
                        p_all.extend(pred.tolist())
                        rows.extend({"dataset": dataset, "task": task.name, "fold": fold["name"],
                                     "evidence": fold["evidence"], "backend": backend, "cutoff": cutoff,
                                     "entity_id": str(entity), "actual": float(actual), "prediction": float(prediction)}
                                    for entity, actual, prediction in zip(y.index, y, pred, strict=True))
                    result["predict_seconds"] = time.perf_counter() - predict_started
                    result["metrics"] = extended_metrics(y_all, p_all, task.kind)
                    result["model_version"] = model.version if model else None
                    if backend == "adaptive":
                        # Selection details are part of the fitted-model audit contract.
                        result["selection_report"] = model.selection
                        for validation in model.selection.get("folds", []):
                            validation_time = timestamp(validation["validation_cutoff"])
                            assert validation_time in set(map(timestamp, fold["train"]))
                            assert all(timestamp(c) in set(map(timestamp, fold["train"])) and
                                       timestamp(c) + pd.Timedelta(days=task.horizon_days + task.label_delay_days)
                                       <= validation_time for c in validation["training_cutoffs"])
                        result["selection_purge_asserted"] = True
                        result["feature_columns"] = model.columns
                    result["error"] = None
                    print(f"{dataset}/{task.name}/{fold['name']}/{backend}: {result['metrics']}", flush=True)
                except Exception as exc:
                    result["error"] = f"{type(exc).__name__}: {exc}"
                    print(f"FAILED {dataset}/{task.name}/{fold['name']}/{backend}: {result['error']}", flush=True)
                result["process_peak_rss_bytes"] = peak_rss()
                results.append(result)
                # Save incremental evidence so a later failure cannot erase earlier outcomes.
                checkpoint = {"dataset": dataset, "protocol_sha256": protocol["protocol_sha256"],
                              "complete": False, "results": results}
                (dest / f"v020-quality-{dataset}.json").write_text(json.dumps(checkpoint, indent=2, allow_nan=False) + "\n")
    report = {"dataset": dataset, "complete": True, "protocol_sha256": protocol["protocol_sha256"],
              "protocol_document_sha256": protocol["document_sha256"], "legacy_commit": LEGACY_COMMIT,
              "current_source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "working_tree_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--", "src/aheaddb"], cwd=ROOT)).hexdigest(),
              "data_version": snapshot.version, "source": config["source"], "folds": config["folds"],
              "source_loading_seconds": loading_seconds, "total_seconds": time.perf_counter() - started,
              "results": results, "failures": sum(row["error"] is not None for row in results),
              "environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__,
                              "numerical_threads": 1},
              "limitations": ["Published folds are exposed retrospective comparisons.",
                              "Later retail outcome horizon overlaps previously exposed outcomes.",
                              "Later Beijing is temporally unused in published folds, not independent external validation.",
                              "Complete-case Beijing label filtering can bias included days.",
                              "Source availability clocks and timezones are experimental assumptions.",
                              "Entities recur; folds and outcome windows are dependent; no IID confidence intervals.",
                              "Process peak RSS is cumulative across loading and preceding fits, not per-model allocation.",
                              "Prediction timing excludes precompiled features; fit timing includes training feature compilation.",
                              "No final-test tuning, paid API/compute, raw-data redistribution or foundation-model claim."]}
    pd.DataFrame(rows).to_csv(dest / f"v020-quality-{dataset}-predictions.csv", index=False)
    (dest / f"v020-quality-{dataset}.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("retail", "beijing", "all"), default="all")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".data")
    parser.add_argument("--dest", type=Path, default=ROOT / "artifacts" / "results")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    if args.freeze_only:
        freeze(args.dest, args.data_dir)
        return
    protocol = json.loads((args.dest / "v020-quality-protocol.json").read_text())
    expected_hash = protocol.pop("protocol_sha256")
    if canonical_hash(protocol) != expected_hash or sha256(PROTOCOL_DOCUMENT) != protocol["document_sha256"]:
        raise ValueError("Frozen protocol or document changed")
    protocol["protocol_sha256"] = expected_hash
    if sha256(Path(__file__)) != protocol["evaluator_sha256"]:
        raise ValueError("Evaluator changed after protocol freeze; disclose and explicitly refreeze before model evaluation")
    with tempfile.TemporaryDirectory(prefix="aheaddb-v010-", dir="/private/tmp") as temp:
        legacy = load_legacy(Path(temp))
        with threadpool_limits(limits=1):
            for dataset in ("retail", "beijing") if args.dataset == "all" else (args.dataset,):
                evaluate(dataset, protocol, args.dest, args.data_dir, legacy)


if __name__ == "__main__":
    main()

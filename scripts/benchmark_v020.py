"""Paired on-host v0.1.0/v0.2.0 refresh and ingestion comparison.

Legacy code is loaded from the immutable public release commit. Each version uses
its own actual feature compiler and histogram models with the same task/data and
hyperparameters. Output equivalence is checked against that version's full path.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import random
import resource
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
from evaluate_v020 import LEGACY_COMMIT, load_legacy
from threadpoolctl import threadpool_limits

from aheaddb.demo import demo_schema, demo_tables, demo_tasks

ROOT = Path(__file__).resolve().parents[1]


def state_bytes(snapshot):
    return {
        "table_bytes": sum(int(x.memory_usage(deep=True).sum()) for x in snapshot._tables.values()),
        "fingerprint_bytes": sum(int(x.memory_usage(deep=True)) for x in snapshot._hashes.values()),
        "owner_index_bytes": sum(
            int(x.memory_usage(deep=True)) for x in getattr(snapshot, "_row_owners", {}).values()
        ),
        "position_index_bytes": sum(
            sys.getsizeof(d) + sum(a.nbytes for a in d.values())
            for d in getattr(snapshot, "_event_positions", {}).values()
        ),
    }


def source_stream(entities):
    tables = demo_tables(entities=entities)
    cutoff = "2024-07-15"
    stream = []

    def save(name):
        stream.append((name, {k: v.copy(deep=True) for k, v in tables.items()}, cutoff))

    def add(key, owner, value=350.0):
        rows = pd.DataFrame(
            [
                {
                    "id": key,
                    "entity_id": owner,
                    "occurred_at": pd.Timestamp("2024-07-10", tz="UTC"),
                    "available_at": pd.Timestamp("2024-07-14", tz="UTC"),
                    "amount": value,
                }
            ]
        )
        tables["orders"] = pd.concat([tables["orders"], rows], ignore_index=True)

    save("no_op")
    add("new", "C0000")
    save("insertion")
    add("late", "C0001")
    save("late_arrival")
    tables["orders"].loc[tables["orders"].id == "new", "amount"] = 900.0
    save("value_update")
    tables["orders"].loc[tables["orders"].id == "new", "entity_id"] = "C0002"
    save("foreign_key_move")
    tables["orders"] = tables["orders"].loc[tables["orders"].id != "late"].copy()
    save("deletion")
    extra = pd.DataFrame(
        [
            {
                "id": f"hub{i}",
                "entity_id": "C0003",
                "occurred_at": pd.Timestamp("2024-07-13", tz="UTC"),
                "available_at": pd.Timestamp("2024-07-14", tz="UTC"),
                "amount": float(i),
            }
            for i in range(120)
        ]
    )
    tables["orders"] = pd.concat([tables["orders"], extra], ignore_index=True)
    save("high_degree_insertion")
    tables["entities"] = tables["entities"].loc[tables["entities"].entity_id != "C0004"].copy()
    tables["orders"] = tables["orders"].loc[tables["orders"].entity_id != "C0004"].copy()
    save("entity_deletion")
    cutoff = "2024-07-22"
    save("clock_advance")
    tables["orders"]["amount"] *= 1.01
    save("dense_update")
    save("model_revision")
    return stream


def run(legacy, entities, repeats):
    stream = source_stream(entities)
    modules = {"v010": legacy, "v020": sys.modules["aheaddb"]}
    snapshots, ingestion, models, tasks = {}, [], {}, {}
    for name, module in modules.items():
        schema = module.Schema.model_validate(demo_schema().model_dump())
        tasks[name] = [module.Task.model_validate(t.model_dump()) for t in demo_tasks()]
        snapshots[name] = []
        for scenario, tables, cutoff in stream:
            wall, cpu = time.perf_counter(), time.process_time()
            snap = module.Snapshot(tables, schema)
            ingestion.append(
                {
                    "version": name,
                    "scenario": scenario,
                    "wall_seconds": time.perf_counter() - wall,
                    "cpu_seconds": time.process_time() - cpu,
                    **state_bytes(snap),
                }
            )
            snapshots[name].append(snap)
        models[name] = [
            module.fit(
                snapshots[name][0],
                task,
                ["2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"],
                "2024-07-01",
                backend="histogram",
            )
            for task in tasks[name]
        ]
    # Independent all-row reference for each version, outside every timing sample.
    refs = {}
    for name, module in modules.items():
        for j, (_, _, cutoff) in enumerate(stream):
            refs[name, j] = [
                model.predict(module.Compiler(snapshots[name][j]).features(task, cutoff))
                for task, model in zip(tasks[name], models[name], strict=True)
            ]
    records = []
    algorithms = [
        ("v010", "incremental"),
        ("v010", "full"),
        ("v020", "incremental"),
        ("v020", "full"),
        ("v020", "cache"),
    ]
    for repeat in range(repeats):
        engines = {}
        for version, mode in algorithms:
            engine = modules[version].Engine(snapshots[version][0])
            for task, model in zip(tasks[version], models[version], strict=True):
                engine.materialize(task, model, "2024-07-15")
            engine.refresh(snapshots[version][0], mode=mode)  # Warm code paths outside measurements.
            engines[version, mode] = engine
        for j, (scenario, _, cutoff) in enumerate(stream):
            if scenario == "model_revision":
                for version in modules:
                    for model in models[version]:
                        model.training_version += f":revision{repeat}"
            for version, mode in random.Random(812 + repeat * 31 + j).sample(algorithms, len(algorithms)):
                engine = engines[version, mode]
                gc.collect()
                start, cpu = time.perf_counter(), time.process_time()
                receipt = engine.refresh(snapshots[version][j], cutoff=cutoff, mode=mode)
                wall_elapsed, cpu_elapsed = time.perf_counter() - start, time.process_time() - cpu
                error = 0.0
                for task, reference in zip(tasks[version], refs[version, j], strict=True):
                    actual = engine.results(task.name).prediction.to_numpy()
                    error = max(error, float(np.max(np.abs(actual - reference))) if len(actual) else 0.0)
                if error != 0:
                    raise AssertionError((version, mode, scenario, error))
                records.append(
                    {
                        "entities": entities,
                        "repeat": repeat,
                        "version": version,
                        "mode": mode,
                        "scenario": scenario,
                        "wall_seconds": wall_elapsed,
                        "cpu_seconds": cpu_elapsed,
                        "max_absolute_error": error,
                        "prediction_rows": receipt["prediction_rows"],
                        "feature_rows": receipt["feature_rows"],
                        "discovery_seconds": receipt["discovery_seconds"],
                        "context_bytes": sum(
                            int(x.memory_usage(deep=True).sum()) for x in engine.contexts.values()
                        ),
                    }
                )
        print(f"{entities} entities repeat {repeat + 1}/{repeats}: exact references", flush=True)
    summaries = []
    frame = pd.DataFrame(records)
    for (version, mode, scenario), group in frame.groupby(["version", "mode", "scenario"], sort=False):
        summaries.append(
            {
                "version": version,
                "mode": mode,
                "scenario": scenario,
                "wall_median_seconds": float(group.wall_seconds.median()),
                "wall_p10_seconds": float(group.wall_seconds.quantile(0.1)),
                "wall_p90_seconds": float(group.wall_seconds.quantile(0.9)),
                "cpu_median_seconds": float(group.cpu_seconds.median()),
                "prediction_rows": int(group.prediction_rows.iloc[0]),
            }
        )
    return {"entities": entities, "records": records, "summaries": summaries, "ingestion": ingestion}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--entities", type=int, nargs="+", default=[400, 2000])
    p.add_argument("--repeats", type=int, default=7)
    p.add_argument("--output", type=Path, default=ROOT / "artifacts/results/v020-refresh.json")
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="aheaddb-refresh-v010-") as folder:
        legacy = load_legacy(Path(folder))
        with threadpool_limits(limits=1):
            result = {
                "legacy_commit": LEGACY_COMMIT,
                "seed": 19,
                "repeats": args.repeats,
                "workloads": [run(legacy, n, args.repeats) for n in args.entities],
            }
    result["source_sha256"] = {
        str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in (ROOT / "src/aheaddb").rglob("*.py")
    }
    result["environment"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
    }
    result["limits"] = [
        "Synthetic direct star workload; no production throughput claim",
        "Each version uses actual own features and histogram estimator: richer v2features are not silently replaced with v1features",
        "Compare within-version full and incremental outputs; predictions need not equal across different feature versions",
        "Data validation/fingerprinting/owner-index construction measured separately from refresh",
        "Ingestion measurements are single observations; all snapshots remain resident; RSS is cumulative",
        "Pandas deep-storage estimates share referenced strings and do not equal unique heap allocation",
        "No paid compute, no new model training inside timed refresh, no test quality claim",
        "Single numerical thread; randomized interleaving per scenario, seven paired repeats, process CPU plus wall time",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(args.output)

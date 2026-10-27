#!/usr/bin/env python3
"""Reproducible synthetic update stress tests; no prediction-quality claims."""
from __future__ import annotations

import argparse
import json
import platform
import random
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd

from aheaddb import Compiler, Engine, Snapshot, fit
from aheaddb.demo import demo_schema, demo_tables, demo_tasks


def benchmark(entities: int = 400, repeats: int = 3) -> dict:
    source = demo_tables(entities=entities)
    schema = demo_schema()
    initial = Snapshot(source, schema)
    tasks = demo_tasks()
    models = [fit(initial, t, ["2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01"], "2024-07-01") for t in tasks]
    snapshots = []
    ingestion_records = []
    tables = {k: v.copy() for k, v in source.items()}
    cutoff = "2024-07-15"

    def save(name):
        begin = time.perf_counter()
        snapshot = Snapshot(tables, schema)
        ingestion_records.append({"scenario": name, "snapshot_seconds": time.perf_counter() - begin,
                                  "row_index_bytes": sum(int(x.memory_usage(deep=True)) for x in snapshot._hashes.values()),
                                  "table_bytes": sum(int(x.memory_usage(deep=True).sum()) for x in snapshot._tables.values())})
        snapshots.append((name, snapshot, cutoff))

    def add(key, owner, occurred, available, amount):
        row = pd.DataFrame([{"id": key, "entity_id": owner, "occurred_at": pd.Timestamp(occurred, tz="UTC"),
                             "available_at": pd.Timestamp(available, tz="UTC"), "amount": amount}])
        tables["orders"] = pd.concat([tables["orders"], row], ignore_index=True)

    save("no_op")
    add("new", "C0000", "2024-07-14", "2024-07-14", 500.)
    save("insertion")
    add("late", "C0001", "2024-07-10", "2024-07-14", 350.)
    save("late_arrival")
    tables["orders"].loc[tables["orders"].id == "new", "amount"] = 900.
    save("value_update")
    tables["orders"].loc[tables["orders"].id == "new", "entity_id"] = "C0002"
    save("foreign_key_move")
    tables["orders"] = tables["orders"].loc[tables["orders"].id != "late"]
    save("deletion")
    for i in range(120):
        add(f"hub{i}", "C0003", "2024-07-13", "2024-07-14", float(i))
    save("high_degree_insertion")
    tables["entities"] = tables["entities"].loc[tables["entities"].entity_id != "C0004"]
    tables["orders"] = tables["orders"].loc[tables["orders"].entity_id != "C0004"]
    save("entity_deletion")
    cutoff = "2024-07-22"
    save("clock_advance")
    # Dense update is an intentional worst case for selective invalidation.
    tables["orders"]["amount"] *= 1.01
    save("dense_update")
    records = []
    for repeat in range(repeats):
        for mode in random.Random(19 + repeat).sample(["full", "cache", "incremental"], 3):
            engine = Engine(initial)
            for task, model in zip(tasks, models, strict=True):
                engine.materialize(task, model, "2024-07-15")
            for scenario, snapshot, t in snapshots:
                start = time.perf_counter()
                receipt = engine.refresh(snapshot, cutoff=t, mode=mode)
                seconds = time.perf_counter() - start
                max_error = 0.
                for task, model in zip(tasks, models, strict=True):
                    reference = model.predict(Compiler(snapshot).features(task, t))
                    observed = engine.results(task.name).prediction.to_numpy()
                    max_error = max(max_error, float(np.max(np.abs(reference - observed))))
                if max_error > 1e-12:
                    raise AssertionError(f"{mode}/{scenario} differs from reference: {max_error}")
                records.append({"repeat": repeat, "scenario": scenario, "mode": mode, "seconds": seconds,
                                "context_bytes": sum(int(x.memory_usage(deep=True).sum())
                                                                              for x in engine.contexts.values()),
                                "max_absolute_error": max_error, **{k: receipt[k] for k in
                                ["discovery_seconds", "changed_source_rows", "feature_rows", "prediction_rows",
                                 "reused_prediction_rows"]}})
    sharing = []
    for repeat in range(repeats):
        for shared in [True, False]:
            engines = [Engine(initial)] if shared else [Engine(initial) for _ in tasks]
            begin = time.perf_counter()
            for i, (task, model) in enumerate(zip(tasks, models, strict=True)):
                engines[0 if shared else i].materialize(task, model, "2024-07-15")
            elapsed = time.perf_counter() - begin
            sharing.append({"repeat": repeat, "shared_context": shared, "seconds": elapsed,
                            "feature_rows": sum(len(x) for e in engines for x in e.contexts.values()),
                            "context_bytes": sum(int(x.memory_usage(deep=True).sum())
                                                 for e in engines for x in e.contexts.values())})
    return {"seed": 19, "entities": entities, "initial_rows": sum(map(len, source.values())),
            "tasks": [t.model_dump() for t in tasks], "records": records, "sharing_records": sharing,
            "ingestion_records": ingestion_records,
            "environment": {"platform": platform.platform(), "python": platform.python_version(),
                            "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                            (1 if platform.system() == "Darwin" else 1024)},
            "method": "Same immutable inputs, fitted models and sequential stream in each mode/repeat; full independent feature+predict reference per step",
            "limits": ["Synthetic star schema; not a production workload", "Single numerical thread; seeded randomized mode order across repeats",
                       "Snapshot loading/validation/fingerprinting excluded; source diff discovery included",
                       "Context bytes measure pandas storage, not all Python allocator overhead; RSS is cumulative process peak",
                       "Full/cache modes skip incremental row-diff discovery; none include source load/validation",
                       "Immutable per-row SHA256 index construction is measured separately, with its retained storage overhead",
                       "Dense updates and advancing clocks intentionally force broad recomputation",
                       "Cache is whole-view data-version memoization; invalidates all on a source change",
                       "Both tasks share context only, never task-conditioned model activations"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", type=int, default=400)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("artifacts/results/refresh.json"))
    args = parser.parse_args()
    result = benchmark(args.entities, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(args.output)

"""Maintained views with source-diff proofs and shared aggregate context.

Diff discovery scans the full snapshot. Reuse avoids feature/model work, not source
scanning. Only declared direct entity/event relations are supported.
"""
from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass

import pandas as pd

from .compiler import Compiler
from .modeling import FittedModel
from .snapshot import Snapshot
from .spec import Task, digest, timestamp


@dataclass
class View:
    task: Task
    model: FittedModel
    cutoff: str
    model_version: str
    context_key: str
    data_version: str
    predictions: pd.DataFrame


class Engine:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self.views: dict[str, View] = {}
        self.contexts: dict[str, pd.DataFrame] = {}
        self.last_refresh: dict = {}

    def _context_key(self, task: Task, cutoff: str) -> str:
        return digest({"schema": self.snapshot.schema.model_dump(), "cutoff": str(timestamp(cutoff)),
                       "history_days": task.history_days, "preprocessing": Compiler.PREPROCESSING})

    def materialize(self, task: Task, model: FittedModel, cutoff: str) -> pd.DataFrame:
        if task != model.task:
            raise ValueError("Model task differs from requested task")
        if digest(self.snapshot.schema.model_dump()) != model.schema_version:
            raise ValueError("Model schema differs from snapshot")
        if timestamp(cutoff) < timestamp(model.trained_as_of):
            raise ValueError("Prediction cutoff precedes model training information time")
        compiler = Compiler(self.snapshot)
        compiler.validate(task)
        key = self._context_key(task, cutoff)
        if key not in self.contexts:
            self.contexts[key] = compiler.features(task, cutoff)
        x = self.contexts[key]
        predictions = pd.DataFrame({"prediction": model.predict(x)}, index=x.index)
        self.views[task.name] = View(task, model, str(timestamp(cutoff)), model.version, key,
                                     self.snapshot.version, predictions)
        return predictions.copy()

    def inspect(self, name: str) -> dict:
        view = self.views[name]
        return {"task": view.task.model_dump(), "cutoff": view.cutoff, "data_version": view.data_version,
                "model_version": view.model_version, "context_key": view.context_key,
                "preprocessing": Compiler.PREPROCESSING, "rows": len(view.predictions),
                "diagnostics": view.model.diagnostics(self.contexts[view.context_key]),
                "model_selection": deepcopy(view.model.selection),
                "cache_key": digest([view.task.version, view.model_version, view.data_version,
                                     view.cutoff, view.context_key]),
                "warnings": ["Predictions are estimates, not facts", "Input provenance is not a causal explanation"]}

    def _affected(self, new: Snapshot) -> tuple[set[str], int]:
        schema = self.snapshot.schema
        affected: set[str] = set()
        changed = 0
        for table in self.snapshot._tables:
            old_hash = self.snapshot.row_hashes(table)
            new_hash = new.row_hashes(table)
            keys = old_hash.index.union(new_hash.index)
            delta = set(keys[old_hash.reindex(keys) != new_hash.reindex(keys)])
            changed += len(delta)
            if table == schema.entity_table:
                affected.update(delta)
            else:
                for snap in [self.snapshot, new]:
                    affected.update(snap._row_owners[table].reindex(list(delta)).dropna())
        return affected, changed

    def refresh(self, new: Snapshot, cutoff: str | None = None, mode: str = "incremental") -> dict:
        if mode not in {"incremental", "full", "cache"}:
            raise ValueError("Refresh mode must be incremental, full or cache")
        start = time.perf_counter()
        if new.schema != self.snapshot.schema:
            # Trained feature schemas cannot silently migrate.
            raise ValueError("Schema changed; create a new engine and retrain")
        # Baselines must not pay incremental discovery costs they do not need.
        changed: int | None
        scan_diff = mode == "incremental" and new.version != self.snapshot.version
        if scan_diff:
            affected, changed = self._affected(new)
        else:
            affected, changed = set(), 0 if mode == "incremental" else None
        discovery = time.perf_counter() - start
        source_changed = new.version != self.snapshot.version
        compiler = Compiler(new)
        context_updates: dict[str, pd.DataFrame] = {}
        pending: dict[str, View] = {}
        recomputed = reused = feature_rows = 0
        # Transactional refresh: no published state changes if computation fails.
        for name, view in self.views.items():
            if view.model.task != view.task or view.model.schema_version != digest(new.schema.model_dump()):
                raise ValueError("Replacement model task/schema differs; materialize a matching view")
            t = str(timestamp(cutoff)) if cutoff is not None else view.cutoff
            if timestamp(t) < timestamp(view.model.trained_as_of):
                raise ValueError("Cutoff precedes model training")
            key = self._context_key(view.task, t)
            all_ids = compiler.entities(t)
            model_version = view.model.version
            force = mode == "full" or t != view.cutoff or model_version != view.model_version
            if mode == "cache" and source_changed:
                force = True
            ids = all_ids if force else sorted((affected | (set(all_ids) - set(view.predictions.index))) & set(all_ids))
            if key not in context_updates:
                # A context may be shared by tasks, but never model activations.
                old = self.contexts.get(key)
                context_force = mode == "full" or t != view.cutoff or old is None or (mode == "cache" and source_changed)
                context_ids = all_ids if context_force or old is None else sorted((affected | (set(all_ids) - set(old.index))) & set(all_ids))
                if not context_ids and not context_force and old is not None:
                    context_updates[key] = old.reindex(all_ids).copy()
                else:
                    fresh = compiler.features(view.task, t, context_ids)
                    feature_rows += len(fresh)
                    kept = pd.DataFrame(columns=fresh.columns) if context_force or old is None else old.drop(index=context_ids, errors="ignore")
                    combined = fresh if kept.empty else kept if fresh.empty else pd.concat([kept, fresh])
                    context_updates[key] = combined.reindex(all_ids).astype(float)
            x = context_updates[key].loc[ids]
            values = view.model.predict(x)
            result = view.predictions.reindex(all_ids).copy()
            result.loc[ids, "prediction"] = values
            if result.isna().any().any():
                raise ValueError("Incomplete refreshed view")
            recomputed += len(ids)
            reused += len(all_ids) - len(ids)
            pending[name] = View(view.task, view.model, t, model_version, key, new.version, result)
        self.snapshot = new
        self.views = pending
        # Drop unreachable contexts on clock advancement; no unbounded old-window cache.
        self.contexts = context_updates
        self.last_refresh = {"mode": mode, "seconds": time.perf_counter() - start,
                             "discovery_seconds": discovery, "changed_source_rows": changed,
                             "diff_scan_performed": scan_diff,
                             "affected_entities": sorted(affected), "feature_rows": feature_rows,
                             "prediction_rows": recomputed, "reused_prediction_rows": reused,
                             "data_version": new.version, "views": list(self.views)}
        return self.last_refresh.copy()

    def results(self, name: str) -> pd.DataFrame:
        return self.views[name].predictions.copy()

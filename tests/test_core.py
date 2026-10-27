"""Independent reference checks for temporal tasks and maintained prediction views."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aheaddb import Compiler, Engine, Event, FittedModel, Outcome, Schema, Snapshot, Task
from aheaddb.modeling import backtest, fit
from aheaddb.spec import digest, timestamp

SCHEMA = Schema(events=(Event(table="events", values=("amount",)),))
CUTOFF = "2026-01-15"


def task(name="sales", kind="regression", history_days=10):
    return Task(name=name, kind=kind, outcome=Outcome(table="events", aggregation="sum", column="amount"),
                horizon_days=2, history_days=history_days, label_delay_days=1,
                metric="mae" if kind == "regression" else "roc_auc")


def tables(n=4):
    entities = pd.DataFrame({"entity_id": [str(i) for i in range(n)], "available_at": ["2026-01-01"] * n})
    events = pd.DataFrame([
        {"id": f"{i}-{j}", "entity_id": str(i), "occurred_at": f"2026-01-{day:02d}",
         "available_at": f"2026-01-{day:02d}", "amount": float(i + j + 1)}
        for i in range(n) for j, day in enumerate([2, 6, 10, 14, 16, 17, 20])
    ])
    return {"entities": entities, "events": events}


@dataclass
class SumEstimator:
    """A deterministic numeric estimator whose output exposes every aggregate."""
    factor: float = 1.0
    fail: bool = False

    def predict(self, x):
        if self.fail:
            raise ValueError("deliberate prediction failure")
        return np.asarray(x) @ np.arange(1, len(x.columns) + 1) * self.factor

    def predict_proba(self, x):
        y = 1 / (1 + np.exp(-self.predict(x) / 100))
        return np.column_stack([1 - y, y])


def model(snapshot, spec):
    return FittedModel(spec, SumEstimator(), list(Compiler(snapshot).features(spec, CUTOFF).columns),
                       "2026-01-10", "test-training", "reference", digest(snapshot.schema.model_dump()))


def engine(snapshot, specs=None):
    result = Engine(snapshot)
    for spec in specs or [task(), task("return", "binary"), task("short", history_days=3)]:
        result.materialize(spec, model(snapshot, spec), CUTOFF)
    return result


def assert_reference(maintained, new, cutoff=CUTOFF):
    for name, view in maintained.views.items():
        x = Compiler(new).features(view.task, cutoff)
        expected = pd.DataFrame({"prediction": view.model.predict(x)}, index=x.index)
        pd.testing.assert_frame_equal(maintained.results(name), expected)
        assert view.data_version == new.version
        assert view.cutoff == str(timestamp(cutoff))
        assert view.model_version == view.model.version


@pytest.mark.parametrize("change", ["insert", "update", "delete", "move", "late", "outside",
                                   "entity_insert", "entity_delete", "new_neighbor"])
def test_supported_changes_match_full_recomputation(change):
    data = tables()
    original = Snapshot(data, SCHEMA)
    maintained = engine(original)
    before_key = maintained.inspect("sales")["cache_key"]
    if change in {"insert", "late", "outside", "new_neighbor"}:
        when = "2026-01-12" if change != "outside" else "2026-01-02"
        avail = "2026-01-16" if change == "late" else "2026-01-12"
        data["events"].loc[len(data["events"])] = ["added", "0", when, avail, 333.0]
    elif change == "update":
        data["events"].loc[data["events"]["id"] == "0-2", "amount"] = 333.0
    elif change == "delete":
        data["events"] = data["events"].loc[data["events"]["id"] != "0-2"]
    elif change == "move":
        data["events"].loc[data["events"]["id"] == "0-2", "entity_id"] = "1"
    elif change == "entity_insert":
        data["entities"].loc[len(data["entities"])] = ["new", "2026-01-03"]
    elif change == "entity_delete":
        data["entities"] = data["entities"].loc[data["entities"]["entity_id"] != "0"]
        data["events"] = data["events"].loc[data["events"]["entity_id"] != "0"]
    new = Snapshot(data, SCHEMA)
    receipt = maintained.refresh(new)
    assert_reference(maintained, new)
    assert maintained.inspect("sales")["cache_key"] != before_key
    if change == "move":
        assert receipt["affected_entities"] == ["0", "1"]
    elif change != "entity_delete":
        assert receipt["affected_entities"] == (["new"] if change == "entity_insert" else ["0"])
    # Two equal-history tasks share aggregate work but run separate model predictions.
    assert len(maintained.contexts) == 2


def test_temporal_boundaries_and_available_time():
    data = tables(1)
    data["events"] = pd.DataFrame([
        ["left", "0", "2026-01-05", "2026-01-05", 1000.0],
        ["inside", "0", "2026-01-06", "2026-01-06", 2.0],
        ["cutoff", "0", "2026-01-15", "2026-01-15", 3.0],
        ["late", "0", "2026-01-14", "2026-01-16", 1000.0],
        ["future", "0", "2026-01-16", "2026-01-15", 1000.0],
    ], columns=data["events"].columns)
    snapshot = Snapshot(data, SCHEMA)
    c = Compiler(snapshot)
    features = c.features(task(), CUTOFF)
    assert features.loc["0", "events__count"] == 2
    assert features.loc["0", "events__amount__sum"] == 5
    assert c.provenance(task(), CUTOFF, "0")["records"]["events"] == ["inside", "cutoff"]


def test_outcome_window_maturity_and_label_delay():
    data = tables(1)
    data["events"] = pd.DataFrame([
        ["cutoff", "0", "2026-01-15", "2026-01-15", 1000.0],
        ["inside", "0", "2026-01-16", "2026-01-17", 2.0],
        ["end", "0", "2026-01-17", "2026-01-18", 3.0],
        ["too_late", "0", "2026-01-16", "2026-01-19", 1000.0],
        ["after", "0", "2026-01-18", "2026-01-18", 1000.0],
    ], columns=data["events"].columns)
    compiler = Compiler(Snapshot(data, SCHEMA))
    with pytest.raises(ValueError, match="not mature"):
        compiler.labels(task(), CUTOFF, "2026-01-17")
    assert compiler.labels(task(), CUTOFF, "2026-01-18").loc["0"] == 5
    assert compiler.labels(task(), CUTOFF, "2026-02-01").loc["0"] == 5


def test_clock_advancement_expires_and_admits_rows_full_refresh():
    snapshot = Snapshot(tables(), SCHEMA)
    maintained = engine(snapshot)
    receipt = maintained.refresh(snapshot, cutoff="2026-01-17")
    assert_reference(maintained, snapshot, "2026-01-17")
    assert receipt["prediction_rows"] == 4 * 3
    assert len(maintained.contexts) == 2


def test_model_mutation_forces_all_predictions_without_source_change():
    snapshot = Snapshot(tables(), SCHEMA)
    maintained = engine(snapshot)
    before = maintained.inspect("sales")["cache_key"]
    maintained.views["sales"].model.estimator.factor = 2
    receipt = maintained.refresh(snapshot)
    assert_reference(maintained, snapshot)
    assert receipt["prediction_rows"] == 4
    assert maintained.inspect("sales")["cache_key"] != before


def test_preprocessing_version_forces_recomputation(monkeypatch):
    snapshot = Snapshot(tables(), SCHEMA)
    maintained = engine(snapshot)
    before = maintained.inspect("sales")["cache_key"]
    monkeypatch.setattr(Compiler, "PREPROCESSING", Compiler.PREPROCESSING + ":test-version")
    receipt = maintained.refresh(snapshot)
    assert_reference(maintained, snapshot)
    assert receipt["prediction_rows"] == 12
    assert receipt["feature_rows"] == 8
    assert maintained.inspect("sales")["cache_key"] != before


@pytest.mark.parametrize("mode,rows", [("incremental", 3), ("full", 12), ("cache", 12)])
def test_refresh_modes_same_results(mode, rows):
    data = tables()
    maintained = engine(Snapshot(data, SCHEMA))
    data["events"].loc[data["events"]["id"] == "0-2", "amount"] = 99
    new = Snapshot(data, SCHEMA)
    result = maintained.refresh(new, mode=mode)
    assert result["prediction_rows"] == rows
    assert_reference(maintained, new)


def test_refresh_is_transactional_when_model_fails():
    data = tables()
    original = Snapshot(data, SCHEMA)
    maintained = engine(original)
    old_results = {name: maintained.results(name) for name in maintained.views}
    old_contexts = {name: frame.copy() for name, frame in maintained.contexts.items()}
    maintained.views["return"].model.estimator.fail = True
    data["events"].loc[data["events"]["id"] == "0-2", "amount"] = 99
    with pytest.raises(ValueError, match="deliberate"):
        maintained.refresh(Snapshot(data, SCHEMA))
    assert maintained.snapshot is original
    assert maintained.last_refresh == {}
    for name, result in old_results.items():
        pd.testing.assert_frame_equal(maintained.results(name), result)
    for name, result in old_contexts.items():
        pd.testing.assert_frame_equal(maintained.contexts[name], result)


def test_defensive_snapshot_and_result_copies():
    data = tables()
    snapshot = Snapshot(data, SCHEMA)
    version = snapshot.version
    data["events"].loc[0, "amount"] = 9999
    external = snapshot.table("events")
    external.loc[0, "amount"] = 9999
    assert snapshot.table("events").loc[0, "amount"] != 9999
    assert snapshot.version == version
    maintained = engine(snapshot)
    output = maintained.results("sales")
    output.iloc[0, 0] = -999
    assert maintained.results("sales").iloc[0, 0] != -999


def test_unknown_schema_and_immature_model_are_rejected():
    snapshot = Snapshot(tables(), SCHEMA)
    spec = task()
    fitted = model(snapshot, spec)
    with pytest.raises(ValueError, match="precedes"):
        Engine(snapshot).materialize(spec, fitted, "2026-01-09")
    with pytest.raises(ValueError, match="differs"):
        Engine(snapshot).materialize(task("different"), fitted, CUTOFF)
    with pytest.raises(ValueError, match="row limit"):
        Snapshot(tables(), SCHEMA, max_rows=2)
    bad = tables()
    bad["events"].loc[0, "entity_id"] = "missing"
    with pytest.raises(ValueError, match="Orphan"):
        Snapshot(bad, SCHEMA)


def test_chronological_backtest_training_labels_mature_at_test_cutoff():
    snapshot = Snapshot(tables(24), SCHEMA)
    spec = task()
    cutoffs = ["2026-01-06", "2026-01-08", "2026-01-10", "2026-01-12", "2026-01-14"]
    report = backtest(snapshot, spec, cutoffs, "2026-01-20", backends=("baseline",), min_train_cutoffs=1)
    for fold in report["folds"]:
        assert fold["error"] is None
        for t in fold["training_cutoffs"]:
            assert timestamp(t) + pd.Timedelta(days=3) <= timestamp(fold["cutoff"])
    with pytest.raises(ValueError, match="not mature"):
        fit(snapshot, spec, ["2026-01-14"], "2026-01-15", "baseline")


@settings(max_examples=30, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["insert", "update", "delete", "move"]),
                         st.integers(0, 3), st.integers(0, 20)), min_size=1, max_size=12))
def test_property_update_stream_equals_full_recomputation(operations):
    data = tables()
    maintained = engine(Snapshot(data, SCHEMA))
    for step, (operation, entity, amount) in enumerate(operations):
        events = data["events"]
        if operation == "insert" or len(events) == 0:
            events.loc[len(events)] = [f"stream-{step}", str(entity), "2026-01-14", "2026-01-14", float(amount)]
        elif operation == "update":
            events.iloc[step % len(events), events.columns.get_loc("amount")] = float(amount)
        elif operation == "move":
            events.iloc[step % len(events), events.columns.get_loc("entity_id")] = str(entity)
        else:
            data["events"] = events.drop(events.index[step % len(events)]).reset_index(drop=True)
        new = Snapshot(data, SCHEMA)
        maintained.refresh(new)
        assert_reference(maintained, new)


def test_deleting_every_entity_and_event_yields_empty_views():
    data = tables()
    maintained = engine(Snapshot(data, SCHEMA))
    data = {name: frame.iloc[:0].copy() for name, frame in data.items()}
    empty = Snapshot(data, SCHEMA)
    maintained.refresh(empty)
    assert_reference(maintained, empty)
    assert all(len(maintained.results(name)) == 0 for name in maintained.views)


def test_calibration_bins_form_a_partition_at_exact_edges():
    from aheaddb.modeling import metrics
    y = np.array([0, 1, 1, 0, 1, 0])
    probabilities = np.array([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    report = metrics(y, probabilities, "binary")
    assert sum(b["n"] for b in report["reliability"]) == len(y)


def test_future_and_late_data_do_not_change_training_predictions():
    data = tables(24)
    snapshot = Snapshot(data, SCHEMA)
    spec = task()
    before = fit(snapshot, spec, ["2026-01-06", "2026-01-10"], "2026-01-13", "histogram")
    data["events"].loc[data["events"]["occurred_at"] > "2026-01-13", "amount"] = 99999.0
    data["events"].loc[len(data["events"])] = ["late-history", "0", "2026-01-02", "2026-01-20", 99999.0]
    data["events"].loc[len(data["events"])] = ["late-label", "0", "2026-01-11", "2026-01-20", 99999.0]
    after_snapshot = Snapshot(data, SCHEMA)
    after = fit(after_snapshot, spec, ["2026-01-06", "2026-01-10"], "2026-01-13", "histogram")
    x = Compiler(snapshot).features(spec, "2026-01-13")
    np.testing.assert_array_equal(before.predict(x), after.predict(x))


def test_missing_numeric_outcome_cannot_become_a_zero_label():
    data = tables()
    data["events"].loc[data["events"]["id"] == "0-4", "amount"] = np.nan
    compiler = Compiler(Snapshot(data, SCHEMA))
    with pytest.raises(ValueError, match="[Mm]issing"):
        compiler.labels(task(), CUTOFF, "2026-01-20")
    # Missing feature values remain visible as an explicit missing-count feature.
    features = compiler.features(task(), "2026-01-17")
    assert features.loc["0", "events__amount__missing"] == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_outcome_threshold_rejected(bad):
    with pytest.raises(ValueError):
        Outcome(table="events", threshold=bad)


def test_schema_changes_require_new_engine_and_retraining():
    snapshot = Snapshot(tables(), SCHEMA)
    maintained = engine(snapshot)
    changed_schema = Schema(events=(Event(table="events"),))
    changed = Snapshot(tables(), changed_schema)
    with pytest.raises(ValueError, match="Schema changed"):
        maintained.refresh(changed)
    with pytest.raises(ValueError, match="schema differs"):
        Engine(changed).materialize(task(), model(snapshot, task()), CUTOFF)
    assert maintained.snapshot is snapshot


@pytest.mark.parametrize("mode", ["incremental", "full", "cache"])
def test_all_refresh_modes_agree_across_noops_clock_and_model_updates(mode, monkeypatch):
    data = tables()
    original = Snapshot(data, SCHEMA)
    maintained = engine(original)
    calls = []
    real_affected = maintained._affected

    def observed_diff(new):
        calls.append(new.version)
        return real_affected(new)

    monkeypatch.setattr(maintained, "_affected", observed_diff)
    receipt = maintained.refresh(original, mode=mode)
    assert_reference(maintained, original)
    assert receipt["prediction_rows"] == (12 if mode == "full" else 0)
    assert receipt["diff_scan_performed"] is False
    assert receipt["changed_source_rows"] == (0 if mode == "incremental" else None)

    data["events"].loc[data["events"]["id"] == "0-2", "amount"] = 999
    changed = Snapshot(data, SCHEMA)
    receipt = maintained.refresh(changed, mode=mode)
    assert_reference(maintained, changed)
    assert receipt["prediction_rows"] == (3 if mode == "incremental" else 12)

    receipt = maintained.refresh(changed, mode=mode)
    assert_reference(maintained, changed)
    assert receipt["prediction_rows"] == (12 if mode == "full" else 0)

    receipt = maintained.refresh(changed, cutoff="2026-01-17", mode=mode)
    assert_reference(maintained, changed, "2026-01-17")
    assert receipt["prediction_rows"] == 12
    assert receipt["feature_rows"] == 8

    maintained.views["sales"].model.estimator.factor = 3
    receipt = maintained.refresh(changed, mode=mode)
    assert_reference(maintained, changed, "2026-01-17")
    assert receipt["prediction_rows"] == (12 if mode == "full" else 4)
    assert len(calls) == (1 if mode == "incremental" else 0)


@pytest.mark.parametrize("mode", ["incremental", "full", "cache"])
@pytest.mark.parametrize("changed_field", ["task", "schema_version"])
def test_refresh_rejects_mismatched_replacement_model_atomically(mode, changed_field):
    snapshot = Snapshot(tables(), SCHEMA)
    maintained = engine(snapshot)
    before = {name: maintained.results(name) for name in maintained.views}
    if changed_field == "task":
        maintained.views["return"].model.task = task("other")
    else:
        maintained.views["return"].model.schema_version = "incompatible-schema"
    with pytest.raises(ValueError, match="Replacement model task/schema differs"):
        maintained.refresh(snapshot, mode=mode)
    assert maintained.snapshot is snapshot
    assert maintained.last_refresh == {}
    for name, result in before.items():
        pd.testing.assert_frame_equal(maintained.results(name), result)


def test_training_range_metadata_is_versioned_and_diagnostics_are_visible():
    snapshot = Snapshot(tables(), SCHEMA)
    maintained = engine(snapshot)
    view = maintained.views["sales"]
    x = Compiler(snapshot).features(view.task, view.cutoff)
    before = view.model.version
    view.model.training_ranges = {str(c): (float(x[c].min()), float(x[c].max())) for c in x}
    assert view.model.version != before
    baseline = maintained.inspect("sales")["diagnostics"]
    assert baseline["rows_outside_training_range"] == 0
    assert baseline["rows_without_history"] == 0
    x.iloc[0, x.columns.get_loc("events__count")] = 1000000
    x.loc["1", :] = 0
    diagnostics = view.model.diagnostics(x)
    assert diagnostics["rows_outside_training_range"] >= 1
    assert diagnostics["rows_without_history"] == 1
    receipt = maintained.refresh(snapshot)
    assert receipt["prediction_rows"] == 4
    assert_reference(maintained, snapshot)


def test_project_backend_selection_and_unknown_task_rejection(tmp_path):
    import json

    import duckdb
    from sklearn.dummy import DummyRegressor
    from sklearn.ensemble import HistGradientBoostingRegressor

    from aheaddb.project import Project

    data = tables(24)
    database = tmp_path / "test.duckdb"
    with duckdb.connect(str(database)) as connection:
        for name, frame in data.items():
            connection.register("source_frame", frame)
            connection.execute(f'CREATE TABLE "{name}" AS SELECT * FROM source_frame')
            connection.unregister("source_frame")
    config = {"database": "test.duckdb", "schema": SCHEMA.model_dump(),
              "tasks": [task("historical").model_dump(), task("learned").model_dump()],
              "training_cutoffs": ["2026-01-06", "2026-01-10"], "training_as_of": "2026-01-13",
              "prediction_cutoff": CUTOFF, "backtest_cutoffs": ["2026-01-06", "2026-01-10", CUTOFF],
              "data_as_of": "2026-01-21", "backend_by_task": {"historical": "baseline"}}
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config))
    project = Project(config_path)
    project.initialize()
    assert isinstance(project.engine.views["historical"].model.estimator, DummyRegressor)
    assert isinstance(project.engine.views["learned"].model.estimator, HistGradientBoostingRegressor)
    assert project.engine.views["historical"].model.backend == "baseline"
    assert project.engine.views["learned"].model.backend == "histogram"
    config["backend_by_task"] = {"historial": "baseline"}
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="unknown task name"):
        Project(config_path)


@pytest.mark.parametrize("mode", ["incremental", "cache"])
def test_unchanged_source_reuses_context_without_diff_or_feature_scan(mode, monkeypatch):
    snapshot = Snapshot(tables(), SCHEMA)
    maintained = engine(snapshot)
    before = {name: maintained.results(name) for name in maintained.views}

    def disallow_scan(*args, **kwargs):
        raise AssertionError("Unchanged-source cache should not scan diff or recompile features")

    monkeypatch.setattr(maintained, "_affected", disallow_scan)
    monkeypatch.setattr(Compiler, "features", disallow_scan)
    receipt = maintained.refresh(snapshot, mode=mode)
    assert receipt["prediction_rows"] == 0
    assert receipt["feature_rows"] == 0
    assert receipt["diff_scan_performed"] is False
    for name, result in before.items():
        pd.testing.assert_frame_equal(maintained.results(name), result)
    # A changed model needs fresh predictions, but its unchanged aggregate input is reusable.
    maintained.views["sales"].model.estimator.factor = 2
    receipt = maintained.refresh(snapshot, mode=mode)
    assert receipt["prediction_rows"] == 4
    assert receipt["feature_rows"] == 0
    pd.testing.assert_frame_equal(maintained.results("sales"), before["sales"] * 2)


def test_cached_row_hash_indexes_are_defensive_and_not_rehashed_on_refresh(monkeypatch):
    data = tables()
    original = Snapshot(data, SCHEMA)
    maintained = engine(original)
    before_version = original.version
    hashes = original.row_hashes("events")
    first_key = hashes.index[0]
    expected_hash = hashes.loc[first_key]
    hashes.loc[first_key] = "caller-overwrite"
    assert original.row_hashes("events").loc[first_key] == expected_hash
    assert original.version == before_version
    data["events"].loc[data["events"]["id"] == "0-2", "amount"] = 999
    changed = Snapshot(data, SCHEMA)

    def disallow_rehash(*args, **kwargs):
        raise AssertionError("Validated immutable source rows should not be rehashed during refresh")

    monkeypatch.setattr(Snapshot, "_compute_row_hashes", disallow_rehash)
    receipt = maintained.refresh(changed)
    assert receipt["diff_scan_performed"] is True
    assert receipt["changed_source_rows"] == 1
    assert_reference(maintained, changed)


@pytest.mark.parametrize('dtype,value', [(np.int64, 6_000_000_000_000_000_000),
                                        (np.uint64, 10_000_000_000_000_000_000)])
@pytest.mark.parametrize('operation', ['features', 'labels', 'binary_labels'])
def test_large_integer_sums_do_not_wrap_before_float_conversion(dtype, value, operation):
    data = tables(1)
    data['events']['amount'] = np.full(len(data['events']), value, dtype=dtype)
    snapshot = Snapshot(data, SCHEMA)
    compiler = Compiler(snapshot)
    if operation == 'features':
        result = compiler.features(task(), CUTOFF).loc['0', 'events__amount__sum']
        expected = float(3 * value)  # Jan6, Jan10, Jan14 inside the history window.
    elif operation == 'labels':
        result = compiler.labels(task(), CUTOFF, '2026-01-20').loc['0']
        expected = float(2 * value)  # Jan16 and Jan17 inside the label window.
    else:
        spec = task(kind='binary').model_copy(update={
            'outcome': Outcome(table='events', aggregation='sum', column='amount', threshold=1.5 * value)})
        result = compiler.labels(spec, CUTOFF, '2026-01-20').loc['0']
        expected = 1.0
    assert result == pytest.approx(expected)
    assert snapshot.table('events')['amount'].dtype == np.dtype(dtype)


@pytest.mark.parametrize("dtype", [np.int64, "Int64"])
@pytest.mark.parametrize("operation", ["features", "labels", "binary_labels"])
def test_integer_sums_preserve_small_residual_after_large_cancellation(dtype, operation):
    data = tables(1)
    days = [6, 10, 14, 16, 16, 17]
    data["events"] = pd.DataFrame({
        "id": [str(i) for i in range(6)], "entity_id": ["0"] * 6,
        "occurred_at": [f"2026-01-{day:02d}" for day in days],
        "available_at": [f"2026-01-{day:02d}" for day in days],
        "amount": pd.Series([2**60, 1, -(2**60)] * 2, dtype=dtype),
    })
    compiler = Compiler(Snapshot(data, SCHEMA))
    if operation == "features":
        actual = compiler.features(task(), CUTOFF).loc["0", "events__amount__sum"]
    else:
        spec = task(kind="binary") if operation == "binary_labels" else task()
        actual = compiler.labels(spec, CUTOFF, "2026-01-20").loc["0"]
    assert actual == 1.0

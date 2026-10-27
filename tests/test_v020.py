"""Independent controls for the v0.2 feature, selection, and refresh changes."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aheaddb import Compiler, Engine, Event, Outcome, Schema, Snapshot, Task
from aheaddb.modeling import PersistenceEstimator, _select, fit
from aheaddb.spec import timestamp

SCHEMA = Schema(events=(Event(table="events", values=("amount",)),))
CUTOFF = "2026-02-15"


def specification(kind="regression"):
    return Task(name="review", kind=kind, horizon_days=2, label_delay_days=1, history_days=40,
                outcome=Outcome(table="events", aggregation="sum", column="amount", threshold=12),
                metric="mae" if kind == "regression" else "roc_auc")


def fixture():
    entities = pd.DataFrame({"entity_id": [str(i) for i in range(24)],
                             "available_at": ["2026-01-01"] * 24})
    rows = [[f"{i}-{day}", str(i), f"2026-01-{day:02d}", f"2026-01-{day:02d}", float(i + day)]
            for i in range(20) for day in (2, 6, 10, 14, 16, 17, 20)]
    return {"entities": entities,
            "events": pd.DataFrame(rows, columns=["id", "entity_id", "occurred_at", "available_at", "amount"])}


def independent_features(snapshot, spec, cutoff, ids):
    """Small row-loop oracle: no groupby or production index/compiler calls."""
    t = timestamp(cutoff)
    rows = snapshot.table("events").to_dict("records")
    result = []
    for owner in ids:
        visible = sorted([r for r in rows if r["entity_id"] == owner
                          and t - pd.Timedelta(days=spec.history_days) < r["occurred_at"] <= t
                          and r["available_at"] <= t],
                         key=lambda r: (r["occurred_at"], r["available_at"], r["id"]))
        values = [r["amount"] for r in visible if not pd.isna(r["amount"])]
        mean = float(np.mean(values)) if values else 0.0
        last = values[-1] if values else 0.0
        record = {"events__count": len(visible),
                  "events__recency": ((t - visible[-1]["occurred_at"]).total_seconds() / 86400
                                      if visible else spec.history_days + 1),
                  "events__amount__sum": sum(values), "events__amount__mean": mean,
                  "events__amount__missing": len(visible) - len(values),
                  "events__amount__std": np.std(values) if values else 0.0,
                  "events__amount__last": last, "events__amount__trend": last - mean}
        for days in (1, 7, 30):
            if days >= spec.history_days:
                continue
            recent = [r for r in visible if r["occurred_at"] > t - pd.Timedelta(days=days)]
            values = [r["amount"] for r in recent if not pd.isna(r["amount"])]
            record.update({f"events__recent{days}__count": len(recent),
                           f"events__recent{days}__amount__sum": sum(values),
                           f"events__recent{days}__amount__mean": np.mean(values) if values else 0.0})
        result.append(record)
    return pd.DataFrame(result, index=pd.Index(ids, name="entity_id")).astype(float)


@settings(max_examples=16, deadline=None)
@given(st.lists(st.one_of(st.none(), st.integers(-100, 100)), min_size=10, max_size=10),
       st.integers(0, 10000), st.sampled_from([1, 3, 7, 10, 30, 40]))
def test_features_match_row_oracle_for_sparse_full_ties_and_permutations(amounts, seed, history_days):
    data = fixture()
    times = ["2026-01-06", "2026-01-16", "2026-02-08", "2026-02-14", "2026-02-15",
             "2026-02-15", "2026-02-15", "2026-02-16", "2026-02-10", "2026-02-14"]
    available = times.copy()
    available[6] = "2026-02-16"  # Future arrival at the cutoff's event time.
    available[7] = "2026-02-14"  # Known row with a future event time.
    available[8] = "2026-02-20"  # Delayed historical row.
    data["events"] = pd.DataFrame([[f"edge-{j}", "0", times[j], available[j], amounts[j]]
                                    for j in range(10)], columns=data["events"].columns)
    data["events"]["amount"] = data["events"]["amount"].astype(float)
    # Include other owners to exercise both sides of the sparse-index threshold.
    data["events"] = pd.concat([data["events"], fixture()["events"]], ignore_index=True)
    snapshot = Snapshot(data, SCHEMA)
    spec = specification().model_copy(update={"history_days": history_days})
    compiler = Compiler(snapshot)
    full = compiler.features(spec, CUTOFF)
    expected = independent_features(snapshot, spec, CUTOFF, list(full.index))
    pd.testing.assert_frame_equal(full, expected, check_exact=False, atol=1e-12, rtol=1e-12)
    for ids in (["0"], ["23", "0", "19"], ["10", "2", "0", "7", "20", "21"]):
        pd.testing.assert_frame_equal(compiler.features(spec, CUTOFF, ids), full.loc[ids])
    data["events"] = data["events"].sample(frac=1, random_state=seed)
    # Preserve nonconsecutive row labels to catch accidental label/position confusion.
    data["events"].index = np.arange(len(data["events"])) * 3 + 17
    reordered = Snapshot(data, SCHEMA)
    assert reordered.version == snapshot.version
    pd.testing.assert_frame_equal(Compiler(reordered).features(spec, CUTOFF), full)


def test_event_index_returns_defensive_rows_and_empty_request():
    snapshot = Snapshot(fixture(), SCHEMA)
    selected = snapshot.event_rows("events", ["0"])
    assert set(selected.entity_id) == {"0"}
    selected.loc[:, "amount"] = -9999
    assert not snapshot.event_rows("events", ["0"]).amount.eq(-9999).any()
    assert snapshot.event_rows("events", []).empty
    assert snapshot.event_rows("events", ["no-such-owner"]).empty


@pytest.mark.parametrize("kind", ["binary", "regression"])
def test_adaptive_fit_ignores_heldout_and_late_training_rows(kind):
    data = fixture()
    original = Snapshot(data, SCHEMA)
    spec = specification(kind)
    cutoffs = ["2026-01-06", "2026-01-08", "2026-01-10", "2026-01-12", "2026-01-14"]
    model = fit(original, spec, cutoffs, "2026-01-17")
    data["events"].loc[data["events"].occurred_at > "2026-01-17", "amount"] = -999999
    data["events"].loc[len(data["events"])] = ["late-history", "0", "2026-01-02", "2026-01-30", 999999.0]
    data["events"].loc[len(data["events"])] = ["late-outcome", "1", "2026-01-11", "2026-01-30", 999999.0]
    after = fit(Snapshot(data, SCHEMA), spec, list(reversed(cutoffs)), "2026-01-17")
    assert model.selection == after.selection
    reference = Compiler(original).features(spec, "2026-01-17")
    np.testing.assert_array_equal(model.predict(reference), after.predict(reference))
    direction = max if kind == "binary" else min
    scores = model.selection["validation_scores"]
    assert model.selection["selected"] == direction(scores, key=scores.get)
    for fold in model.selection["folds"]:
        valid = timestamp(fold["validation_cutoff"])
        assert valid < timestamp("2026-01-17")
        assert all(timestamp(t) + pd.Timedelta(days=3) <= valid for t in fold["training_cutoffs"])


def test_inner_selection_actual_fit_inputs_exclude_immature_cutoffs(monkeypatch):
    import aheaddb.modeling as modeling

    times = [timestamp(f"2026-01-{day:02d}") for day in (1, 3, 5, 7, 9)]
    xs = [pd.DataFrame({"cutoff_number": [j] * 24}) for j in range(5)]
    ys = [pd.Series([j] * 24, dtype=float) for j in range(5)]
    observed = []

    class Recorder:
        def fit(self, x, y):
            observed.append((set(x.cutoff_number), set(y)))
            return self

        def predict(self, x):
            return np.zeros(len(x))

    monkeypatch.setattr(modeling, "_estimator", lambda *_: Recorder())
    chosen, report = _select(specification(), xs, ys, times)
    assert chosen == "baseline"  # Deterministic candidate-order tie break.
    # Each of five candidates is fitted once. The later validation date must
    # not admit cutoff 2, even though its outcomes have matured by that date.
    assert observed == [({0, 1}, {0.0, 1.0})] * 5
    assert {fold["validation_cutoff"] for fold in report["folds"]} == {str(times[3]), str(times[4])}
    assert len(report["folds"]) == 10
    assert all(fold["training_cutoffs"] == [str(times[0]), str(times[1])] for fold in report["folds"])


@pytest.mark.parametrize("backend", ["histogram", "robust", "linear"])
def test_serialized_version_detects_estimator_state_and_is_stable_on_predict(backend):
    snapshot = Snapshot(fixture(), SCHEMA)
    spec = specification("binary" if backend == "linear" else "regression")
    model = fit(snapshot, spec, ["2026-01-08", "2026-01-12"], "2026-01-15", backend)
    version = model.version
    features = Compiler(snapshot).features(spec, "2026-01-17")
    before = model.predict(features)
    assert model.version == version
    if backend == "linear":
        model.estimator[-1].coef_[0, 0] += 1
    else:
        model.estimator._baseline_prediction[0, 0] += 1
    assert model.version != version
    assert not np.array_equal(model.predict(features), before)


@pytest.mark.parametrize("operation", ["move", "delete", "insert", "availability", "occurred"])
def test_indexed_refresh_matches_full_after_sparse_changes_and_clock_advance(operation):
    data = fixture()
    original = Snapshot(data, SCHEMA)
    spec = specification()
    model = fit(original, spec, ["2026-01-06", "2026-01-10"], "2026-01-13", "histogram")
    maintained = Engine(original)
    maintained.materialize(spec, model, "2026-01-15")
    mask = data["events"].id.eq("0-14")
    if operation == "move":
        data["events"].loc[mask, "entity_id"] = "23"
    elif operation == "delete":
        data["events"] = data["events"].loc[~mask]
    elif operation == "insert":
        data["events"].loc[len(data["events"])] = ["new", "23", "2026-01-14", "2026-01-14", 99999.0]
    elif operation == "availability":
        data["events"].loc[mask, "available_at"] = "2026-01-16"
    else:
        data["events"].loc[mask, "occurred_at"] = "2026-01-16"
    updated = Snapshot(data, SCHEMA)
    receipt = maintained.refresh(updated)
    assert receipt["feature_rows"] == (2 if operation == "move" else 1)
    if operation == "move":
        assert receipt["affected_entities"] == ["0", "23"]
    for cutoff in ("2026-01-15", "2026-01-17", "2026-03-01"):
        maintained.refresh(updated, cutoff=cutoff)
        expected = Engine(updated)
        expected.materialize(spec, model, cutoff)
        pd.testing.assert_frame_equal(maintained.results(spec.name), expected.results(spec.name))
        pd.testing.assert_frame_equal(maintained.contexts[maintained.views[spec.name].context_key],
                                      Compiler(updated).features(spec, cutoff))


def test_inspection_selection_metadata_is_defensive():
    snapshot = Snapshot(fixture(), SCHEMA)
    spec = specification()
    model = fit(snapshot, spec, ["2026-01-06", "2026-01-10", "2026-01-14"], "2026-01-17")
    engine = Engine(snapshot)
    engine.materialize(spec, model, "2026-01-17")
    version = model.version
    metadata = engine.inspect(spec.name)["model_selection"]
    metadata["selected"] = "caller-overwrite"
    metadata["folds"][0]["training_cutoffs"].clear()
    metadata["validation_scores"].clear()
    assert model.version == version
    assert engine.inspect(spec.name)["model_selection"]["selected"] != "caller-overwrite"


def test_single_mature_training_cutoff_falls_back_without_selection():
    model = fit(Snapshot(fixture(), SCHEMA), specification(), ["2026-01-14"], "2026-01-17")
    assert model.selection["selected"] == "baseline"
    assert model.selection["limitation"]
    assert model.selection["folds"] == []
    assert model.selection["validation_scores"] == {}


@pytest.mark.parametrize("n_times,block_size", [(3, 2), (5, 2), (20, 4), (70, 12)])
def test_selector_block_budget_uses_one_fixed_mature_prefix(monkeypatch, n_times, block_size):
    import aheaddb.modeling as modeling

    times = [timestamp("2026-01-01") + pd.Timedelta(days=4 * j) for j in range(n_times)]
    xs = [pd.DataFrame({"cutoff_number": [j] * 24}) for j in range(n_times)]
    ys = [pd.Series([j] * 24, dtype=float) for j in range(n_times)]
    observed = []

    class Recorder:
        def fit(self, x, y):
            observed.append(set(x.cutoff_number))
            return self

        def predict(self, x):
            return np.zeros(len(x))

    monkeypatch.setattr(modeling, "_estimator", lambda *_: Recorder())
    _, report = _select(specification(), xs, ys, times)
    first = n_times - block_size
    assert observed == [set(range(first))] * 5
    assert report["validation_cutoffs"] == [str(t) for t in times[first:]]
    assert len(report["folds"]) == 5 * block_size


@pytest.mark.parametrize("single_class_dates", [False, True])
def test_pooled_auc_is_computed_across_dates_not_average_date_auc(monkeypatch, single_class_dates):
    import aheaddb.modeling as modeling

    times = [timestamp(f"2026-01-{day:02d}") for day in (1, 5, 9)]
    xs = [pd.DataFrame({"prediction": [.8, .9] * 12}),
          pd.DataFrame({"prediction": [.8, .9]}), pd.DataFrame({"prediction": [.1, .2]})]
    ys = [pd.Series([0., 1.] * 12), pd.Series([0., 1.]), pd.Series([0., 1.])]
    if single_class_dates:
        ys[1], ys[2] = pd.Series([1., 1.]), pd.Series([0., 0.])

    class RecordedProbability:
        def fit(self, x, y):
            return self

        def predict_proba(self, x):
            p = np.asarray(x.prediction)
            return np.column_stack([1 - p, p])

    monkeypatch.setattr(modeling, "_estimator", lambda *_: RecordedProbability())
    selected, report = _select(specification("binary"), xs, ys, times)
    assert selected == "baseline"
    assert all(fold["score"] == (None if single_class_dates else 1) for fold in report["folds"])
    # Mixed dates have three correctly ranked pairs out of four; single-class
    # dates have four, despite undefined per-date ranking metrics.
    assert set(report["validation_scores"].values()) == {1. if single_class_dates else .75}


@pytest.mark.parametrize("aggregation", ["count", "sum"])
def test_persistence_regression_projection_has_exact_units_and_order(aggregation):
    spec = specification().model_copy(update={"history_days": 10,
        "outcome": Outcome(table="events", aggregation=aggregation,
                           column="amount" if aggregation == "sum" else None)})
    x = pd.DataFrame({"events__count": [0., 10., 5., 20.],
                      "events__amount__last": [900., 3., -4., 0.]}, index=["z", "c", "a", "b"])
    estimator = PersistenceEstimator(spec).fit(x, pd.Series([999.] * 4))
    expected = [0., 2., 1., 4.] if aggregation == "count" else [0., 6., -4., 0.]
    np.testing.assert_array_equal(estimator.predict(x), expected)
    np.testing.assert_array_equal(estimator.predict(x.iloc[[2, 0]]), np.asarray(expected)[[2, 0]])


@pytest.mark.parametrize("kind", ["binary", "regression"])
def test_persistence_training_and_calibration_ignore_future_and_late_data(kind):
    data = fixture()
    original = Snapshot(data, SCHEMA)
    spec = specification(kind)
    cutoffs = ["2026-01-08", "2026-01-12"]
    before = fit(original, spec, cutoffs, "2026-01-15", "persistence")
    x = Compiler(original).features(spec, "2026-01-15")
    predictions = before.predict(x)
    assert np.isfinite(predictions).all()
    if kind == "binary":
        assert ((predictions >= 0) & (predictions <= 1)).all()
        assert before.estimator.calibrator[-1].n_features_in_ == 1
        version = before.version
        before.estimator.calibrator[-1].coef_[0, 0] += 1
        assert before.version != version
    data["events"].loc[data["events"].occurred_at > "2026-01-15", "amount"] = 999999
    data["events"].loc[len(data["events"])] = ["delayed", "0", "2026-01-02", "2026-01-30", -999999.]
    data["events"].loc[len(data["events"])] = ["delayed-label", "1", "2026-01-13", "2026-01-30", 999999.]
    after = fit(Snapshot(data, SCHEMA), spec, cutoffs, "2026-01-15", "persistence")
    np.testing.assert_array_equal(after.predict(x), predictions)


def test_selected_candidate_is_refitted_on_all_mature_outer_training_data(monkeypatch):
    import aheaddb.modeling as modeling

    snapshot = Snapshot(fixture(), SCHEMA)
    spec = specification()
    cutoffs = ["2026-01-06", "2026-01-08", "2026-01-10", "2026-01-12", "2026-01-14"]
    compiler = Compiler(snapshot)
    observed = []

    class Recorder:
        def fit(self, x, y):
            observed.append((x.copy(), y.copy()))
            return self

        def predict(self, x):
            return np.zeros(len(x))

    monkeypatch.setattr(modeling, "_estimator", lambda *_: Recorder())
    fit(snapshot, spec, cutoffs, "2026-01-17")
    assert len(observed) == 6
    for actual_x, actual_y in observed[:5]:
        pd.testing.assert_frame_equal(actual_x, pd.concat([compiler.features(spec, t) for t in cutoffs[:2]]))
        pd.testing.assert_series_equal(actual_y, pd.concat([compiler.labels(spec, t, "2026-01-12")
                                                         for t in cutoffs[:2]]))
    pd.testing.assert_frame_equal(observed[-1][0], pd.concat([compiler.features(spec, t) for t in cutoffs]))
    pd.testing.assert_series_equal(observed[-1][1], pd.concat([compiler.labels(spec, t, "2026-01-17")
                                                           for t in cutoffs]))

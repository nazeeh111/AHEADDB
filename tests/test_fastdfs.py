"""Optional dependency checks; these execute in the isolated FastDFS environment."""
import importlib.util

import pandas as pd
import pytest

from aheaddb import Event, Outcome, Schema, Snapshot, Task

pytestmark = pytest.mark.skipif(importlib.util.find_spec("fastdfs") is None, reason="optional fastdfs not installed")


def fixture_snapshot(extra=None):
    events = pd.DataFrame({"id": ["old", "seen", "late", "future"], "entity_id": ["a"] * 4,
                           "occurred_at": ["2020-01-01", "2020-01-10", "2020-01-10", "2020-02-10"],
                           "available_at": ["2020-01-01", "2020-01-10", "2020-02-10", "2020-01-10"],
                           "amount": [100000., 2., 100000., 100000.]})
    if extra is not None:
        events.loc[2:, "amount"] = extra
    entities = pd.DataFrame({"entity_id": ["a", "b"], "available_at": ["2020-01-01"] * 2,
                             "future_label": [1e8, -1e8]})
    schema = Schema(events=(Event(table="events", values=("amount",)),))
    return Snapshot({"entities": entities, "events": events}, schema)


def task():
    return Task(name="future", kind="regression", outcome=Outcome(table="events", aggregation="sum",
                column="amount"), history_days=15, horizon_days=7, metric="mae")


def test_fastdfs_respects_both_clocks_window_and_undeclared_attributes():
    from aheaddb.fastdfs_backend import features
    a = features(fixture_snapshot(), task(), "2020-01-20")
    b = features(fixture_snapshot(extra=-1e10), task(), "2020-01-20")
    pd.testing.assert_frame_equal(a, b)
    assert list(a.index) == ["a", "b"]
    assert not any("future_label" in c or "entity_id" in c for c in a.columns)
    assert any("SUM" in c.upper() and a.loc["a", c] == 2 for c in a.columns)
    assert not (a.abs() >= 100000).any().any()


def test_empty_history_is_a_finite_zero_aggregate():
    from aheaddb.fastdfs_backend import features
    data = features(fixture_snapshot(), task(), "2020-01-02")
    assert list(data.index) == ["a", "b"]
    assert data.loc["b"].eq(0).all()


def test_all_history_expired_is_finite_zero():
    from aheaddb.fastdfs_backend import features
    data = features(fixture_snapshot(), task(), "2020-02-01")
    assert data.eq(0).all().all()


def test_label_maturity_checked_before_feature_work():
    from aheaddb.fastdfs_backend import fit_fastdfs
    with pytest.raises(ValueError, match="not mature"):
        fit_fastdfs(fixture_snapshot(), task(), ["2020-01-20"], "2020-01-21")


def test_prediction_cannot_use_a_model_trained_in_its_future():
    from aheaddb.fastdfs_backend import FastDFSModel
    from aheaddb.spec import digest
    snapshot = fixture_snapshot()
    model = FastDFSModel(task(), None, [], digest(snapshot.schema.model_dump()), "test-only", "2020-01-30")
    with pytest.raises(ValueError, match="precedes model training"):
        model.predict(snapshot, "2020-01-20")


def test_upstream_memory_sentinel_cannot_delete_a_colliding_file(tmp_path, monkeypatch):
    from aheaddb.fastdfs_backend import features
    monkeypatch.chdir(tmp_path)
    sentinel = tmp_path / ":memory:"
    sentinel.write_bytes(b"existing user bytes")
    with pytest.raises(ValueError, match="file named"):
        features(fixture_snapshot(), task(), "2020-01-20")
    assert sentinel.read_bytes() == b"existing user bytes"

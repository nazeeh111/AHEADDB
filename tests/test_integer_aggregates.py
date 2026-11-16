"""Integer aggregate results against Python's arbitrary-precision arithmetic."""
import pandas as pd
import pytest

from aheaddb import Compiler, Event, Outcome, Schema, Snapshot, Task


def compiler_for(values, dtype, *, labels=False):
    day = "2026-02-02" if labels else "2026-01-31"
    events = pd.DataFrame({
        "id": [str(i) for i in range(len(values))],
        "entity_id": ["seen"] * len(values),
        "occurred_at": [f"{day}T{18 + i:02d}:00:00Z" for i in range(len(values))],
        "available_at": [f"{day}T{18 + i:02d}:00:00Z" for i in range(len(values))],
        "amount": pd.Series(values, dtype=dtype),
    })
    snapshot = Snapshot({
        "entities": pd.DataFrame({"entity_id": ["seen", "empty"],
                                  "available_at": ["2026-01-01"] * 2}),
        "events": events,
    }, Schema(events=(Event(table="events", values=("amount",)),)))
    return Compiler(snapshot)


def aggregate_task(*, binary=False, threshold=0):
    return Task(name="amount", kind="binary" if binary else "regression",
                outcome=Outcome(table="events", aggregation="sum", column="amount", threshold=threshold),
                history_days=60, horizon_days=2, label_delay_days=1,
                metric="roc_auc" if binary else "mae")


@pytest.mark.parametrize("values,dtype", [
    ([6_000_000_000_000_000_000] * 3, "int64"),
    ([10_000_000_000_000_000_000] * 3, "uint64"),
    ([6_000_000_000_000_000_000] * 3, "Int64"),
    ([10_000_000_000_000_000_000] * 3, "UInt64"),
    ([2**60, 1, -(2**60)], "int64"),
    ([2**60, 1, -(2**60)], "Int64"),
])
@pytest.mark.parametrize("labels", [False, True])
def test_integer_sums_match_exact_reference_in_every_window(values, dtype, labels):
    if labels:
        compiler = compiler_for(values, dtype, labels=True)
        actual = compiler.labels(aggregate_task(), "2026-02-01", "2026-02-05")
        assert actual.loc["seen"] == float(sum(values))
        assert actual.loc["empty"] == 0
        return
    compiler = compiler_for(values, dtype)
    actual = compiler.features(aggregate_task(), "2026-02-01")
    expected = float(sum(values))
    for prefix in ["events", "events__recent1", "events__recent7", "events__recent30"]:
        assert actual.loc["seen", f"{prefix}__amount__sum"] == expected
        assert actual.loc["empty", f"{prefix}__amount__sum"] == 0
    assert str(compiler.snapshot.table("events")["amount"].dtype) == dtype


@pytest.mark.parametrize("dtype", ["int64", "uint64", "Int64", "UInt64"])
@pytest.mark.parametrize("binary", [False, True])
def test_outcome_sum_preserves_integer_cancellation_and_threshold(dtype, binary):
    values = [2**60, 1, 0]
    compiler = compiler_for(values, dtype, labels=True)
    result = compiler.labels(aggregate_task(binary=binary, threshold=2**60),
                             "2026-02-01", "2026-02-05")
    expected = float(sum(values) > 2**60) if binary else float(sum(values))
    assert result.loc["seen"] == expected
    assert result.loc["empty"] == 0


@pytest.mark.parametrize("dtype", ["Int64", "UInt64"])
def test_nullable_integer_features_preserve_missing_count_and_zero_sum(dtype):
    actual = compiler_for([None, None, None], dtype).features(aggregate_task(), "2026-02-01")
    assert actual.loc["seen", "events__amount__sum"] == 0
    assert actual.loc["seen", "events__amount__missing"] == 3
    assert actual.loc["empty", "events__amount__missing"] == 0


def test_float_sum_nan_fill_behavior_remains_compatible():
    actual = compiler_for([1e308, 1e308, -1e308], "float64").features(aggregate_task(), "2026-02-01")
    for prefix in ["events", "events__recent1", "events__recent7", "events__recent30"]:
        assert actual.loc["seen", f"{prefix}__amount__sum"] == 0

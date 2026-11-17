"""Source transport must preserve values before exact aggregate arithmetic."""
import json

import pandas as pd
import pytest

from aheaddb import Compiler, Event, Outcome, Schema, Snapshot, Task
from aheaddb.live.store import decode_snapshot, encode_snapshot


def source(values, dtype, *, future=False):
    day = "2026-02-02" if future else "2026-01-31"
    return Snapshot({
        "entities": pd.DataFrame({"entity_id": ["a"], "available_at": ["2026-01-01"]}),
        "events": pd.DataFrame({
            "id": [str(i) for i in range(len(values))], "entity_id": ["a"] * len(values),
            "occurred_at": [day] * len(values), "available_at": [day] * len(values),
            "amount": pd.Series(values, dtype=dtype),
        }),
    }, Schema(events=(Event(table="events", values=("amount",)),)))


def task(binary=False):
    return Task(name="amount", kind="binary" if binary else "regression",
                outcome=Outcome(table="events", aggregation="sum", column="amount"),
                history_days=60, horizon_days=2, metric="roc_auc" if binary else "mae")


@pytest.mark.parametrize("values,dtype", [
    ([2**60 + 1, -(2**60), None], "Int64"),
    ([2**63 - 1, None], "Int64"),
    ([2**64 - 1, None], "UInt64"),
    ([None, None], "Int64"),
    ([0.1, None, -0.25], "float64"),
    ([2**60 + 1, -(2**60)], "int64"),
])
def test_json_roundtrip_preserves_declared_numeric_values_and_features(values, dtype):
    original = source(values, dtype)
    payload = encode_snapshot(original)
    restored = decode_snapshot(json.loads(json.dumps(payload)))
    # JSON text equality also distinguishes integers from floats, unlike Python ==.
    assert json.dumps(encode_snapshot(restored), sort_keys=True) == json.dumps(payload, sort_keys=True)
    if any(value is not None for value in values):
        assert restored.version == original.version
    pd.testing.assert_frame_equal(Compiler(restored).features(task(), "2026-02-01"),
                                  Compiler(original).features(task(), "2026-02-01"))
    if dtype == "Int64" and values[0] is not None and len(values) == 3:
        assert Compiler(restored).features(task(), "2026-02-01").loc["a", "events__amount__sum"] == 1


@pytest.mark.parametrize("binary", [False, True])
def test_roundtrip_exact_cancellation_reaches_regression_and_binary_labels(binary):
    original = source([2**60 + 1, -(2**60)], "Int64", future=True)
    restored = decode_snapshot(encode_snapshot(original))
    assert Compiler(restored).labels(task(binary), "2026-02-01", "2026-02-05").loc["a"] == 1


def test_decode_refuses_mixed_numeric_column_that_would_round_integer():
    payload = encode_snapshot(source([1, 2, None], "Int64"))
    payload["tables"]["events"][0]["amount"] = 2**60 + 1
    payload["tables"]["events"][1]["amount"] = 0.5
    before = json.dumps(payload, sort_keys=True)
    with pytest.raises(ValueError, match="lose|represent"):
        decode_snapshot(payload)
    assert json.dumps(payload, sort_keys=True) == before


def test_decode_accepts_exact_mixed_column_and_empty_relation():
    payload = encode_snapshot(source([1, 2, None], "Int64"))
    payload["tables"]["events"][1]["amount"] = 0.5
    restored = decode_snapshot(payload)
    assert Compiler(restored).features(task(), "2026-02-01").loc["a", "events__amount__sum"] == 1.5
    payload["tables"]["events"] = []
    assert Compiler(decode_snapshot(payload)).features(task(), "2026-02-01").loc["a", "events__amount__sum"] == 0


@pytest.mark.parametrize("values", [[-(2**63)-1, None], [2**64, None], [-1, 2**63, None]])
def test_decode_refuses_integer_column_outside_supported_domain(values):
    payload = encode_snapshot(source([1, 2, None], "Int64"))
    payload["tables"]["events"] = payload["tables"]["events"][:len(values)]
    for row, value in zip(payload["tables"]["events"], values, strict=True):
        row["amount"] = value
    with pytest.raises(ValueError, match="represent"):
        decode_snapshot(payload)

"""Deterministic contracts for planner fairness and lossless durable encoding."""
# ruff: noqa: E402  # Live is an optional extra.
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

pytest.importorskip("psycopg")

from aheaddb import Snapshot
from aheaddb.demo import demo_schema
from aheaddb.live.planner import Candidate, choose
from aheaddb.live.store import decode_snapshot, encode_snapshot


def test_lossless_float_snapshot():
    tables = {"entities": pd.DataFrame([{"entity_id": "a", "available_at": "2024-01-01"}]),
              "orders": pd.DataFrame([{"id": "e", "entity_id": "a", "occurred_at": "2024-01-02",
                                       "available_at": "2024-01-02", "amount": 1.1234567890123457}])}
    snapshot = Snapshot(tables, demo_schema())
    restored = decode_snapshot(encode_snapshot(snapshot))
    assert restored.version == snapshot.version
    assert restored.table("orders").amount.iloc[0] == 1.1234567890123457


def test_budget_fairness_age_overrides_recurring_urgent_context():
    now = datetime.now(timezone.utc)
    hot = Candidate(2, "hot", 10, now - timedelta(seconds=0.5), now, "hot", 0.1)
    old = Candidate(1, "cold", 2, now - timedelta(seconds=2), now + timedelta(seconds=100), "cold", 0.1)
    assert choose([hot, old], now, "budget", fairness_seconds=1) == [1]


def test_coalescing_preserves_earliest_deadline_and_shared_semantics():
    now = datetime.now(timezone.utc)
    old = Candidate(1, "a", 1, now - timedelta(seconds=2), now, "shared", 0.1)
    new = Candidate(3, "a", 2, now, now + timedelta(seconds=60), "shared", 0.1)
    shared = Candidate(4, "b", 2, now, now + timedelta(seconds=60), "shared", 0.1)
    other = Candidate(5, "c", 2, now, now + timedelta(seconds=60), "different", 0.1)
    assert choose([old, new, shared, other], now, "budget") == [3, 4]
    assert choose([old, new, shared], now, "fifo") == [1]


def test_source_silence_and_busy_debounce_bounded_wait():
    now = datetime.now(timezone.utc)
    recent = Candidate(1, "a", 1, now, now + timedelta(seconds=60), "c", 0.1)
    assert choose([recent], now, "budget") == []
    old = Candidate(0, "a", 0, now - timedelta(seconds=2), now + timedelta(seconds=59), "c", 0.1)
    assert choose([old, recent], now, "debounce") == [1]


def test_fifo_already_shares_compatible_dependencies():
    now = datetime.now(timezone.utc)
    a = Candidate(1, "a", 1, now, now, "same", 0.1)
    b = Candidate(2, "b", 1, now, now, "same", 0.1)
    assert choose([a, b], now, "fifo") == [1, 2]
    assert choose([a, b], now, "full") == [1, 2]

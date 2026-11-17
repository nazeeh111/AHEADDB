"""The example must render freshness from one complete grouped observation."""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("psycopg")

SPEC = importlib.util.spec_from_file_location(
    "live_desk_example", Path(__file__).parents[1] / "examples/live_desk/app.py")
assert SPEC and SPEC.loader
EXAMPLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXAMPLE)


@pytest.mark.parametrize("states", [
    ("fresh", "fresh"), ("stale", "stale"), ("stale", "fresh"), ("unknown", "unknown"),
])
@pytest.mark.parametrize("allow_stale", [False, True])
def test_example_current_claim_uses_group_coverage(monkeypatch, states, allow_stale):
    names = ("return_14d", "revenue_14d")

    class Client:
        def read_group(self, requested, *, max_age_seconds, allow_stale):
            assert requested == names
            assert max_age_seconds == 60
            answers = {
                name: SimpleNamespace(
                    coverage=SimpleNamespace(source_id="owned-source", current_revision=2, state=state),
                    model_dump=lambda **kwargs: {"generation": "batch"},
                )
                for name, state in zip(names, states, strict=True)
            }
            return SimpleNamespace(answers=answers)

    @contextmanager
    def client():
        yield Client()

    monkeypatch.setattr(EXAMPLE, "Client", client)
    request = object.__new__(EXAMPLE.App)
    request.headers = {"Host": "127.0.0.1:8791"}
    request.path = "/features?allow_stale=" + str(allow_stale).lower()
    result = []
    request.reply = lambda code, payload: result.append((code, payload))
    request.do_GET()
    code, payload = result[0]
    current = states == ("fresh", "fresh")
    assert code == (200 if current or allow_stale else 503)
    if code == 200:
        assert payload["covers_observed_head"] == current
        assert payload["same_source_boundary"]
        assert payload["status"] == {"source_id": "owned-source", "revision": 2,
                                     "clock_unknown": "unknown" in states}


def test_order_retry_uses_identical_durable_mutation_and_response(monkeypatch):
    import io
    import json

    request_id = "e0bb2150-dc3e-48fb-9a6d-d6aa997da861"
    edits, replies = [], []

    class Client:
        def edit(self, edit):
            edits.append(edit)
            return 7

    @contextmanager
    def client():
        yield Client()

    monkeypatch.setattr(EXAMPLE, "Client", client)
    for _ in range(2):
        body = json.dumps({"request_id": request_id, "entity": "C0000", "amount": 450}).encode()
        request = object.__new__(EXAMPLE.App)
        request.headers = {"Host": "127.0.0.1:8791", "Content-Length": str(len(body))}
        request.path, request.rfile = "/orders", io.BytesIO(body)
        request.reply = lambda code, payload: replies.append((code, payload))
        request.do_POST()
    assert len(edits) == 2
    assert edits[0] == edits[1]
    assert edits[0].request_id == request_id
    assert edits[0].mutations[0].key == "LIVE-" + request_id
    assert replies == [(200, {"revision": 7, "order": "LIVE-" + request_id, "request_id": request_id})] * 2


@pytest.mark.parametrize("request_id", [None, "", "not-a-uuid", "E0BB2150-DC3E-48FB-9A6D-D6AA997DA861", 123])
def test_order_requires_a_canonical_caller_request_identity(monkeypatch, request_id):
    import io
    import json

    @contextmanager
    def forbidden():
        raise AssertionError("Invalid identity must not call the source")
        yield

    monkeypatch.setattr(EXAMPLE, "Client", forbidden)
    body = json.dumps({"request_id": request_id, "entity": "C0000", "amount": 450}).encode()
    request = object.__new__(EXAMPLE.App)
    request.headers = {"Host": "127.0.0.1:8791", "Content-Length": str(len(body))}
    request.path, request.rfile = "/orders", io.BytesIO(body)
    replies = []
    request.reply = lambda code, payload: replies.append((code, payload))
    request.do_POST()
    assert replies[0][0] == 422

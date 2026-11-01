"""The example must honor newer source evidence after its separate feature reads."""
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


@pytest.mark.parametrize("change", ["none", "revision", "source", "definition", "clock"])
@pytest.mark.parametrize("allow_stale", [False, True])
def test_later_status_controls_example_current_claim(monkeypatch, change, allow_stale):
    names = ("return_14d", "revenue_14d")
    status = {"source_id": "owned-source", "revision": 1, "clock_unknown": False,
              "views": [{"name": name, "generation": "batch"} for name in names]}
    if change == "revision":
        status["revision"] = 2
    elif change == "source":
        status["source_id"] = "replaced-source"
    elif change == "definition":
        status["views"][0]["generation"] = None
    elif change == "clock":
        status["clock_unknown"] = True

    class Client:
        def read(self, name, **kwargs):
            return SimpleNamespace(coverage=SimpleNamespace(source_id="owned-source", revision=1),
                                   task=SimpleNamespace(name=name), generation="batch",
                                   model_dump=lambda **kwargs: {"generation": "batch"})

        def status(self):
            return status

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
    assert code == (200 if change == "none" or allow_stale else 503)
    if code == 200:
        assert payload["covers_observed_head"] == (change == "none")

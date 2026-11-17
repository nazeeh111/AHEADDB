"""The client preserves its unavailable contract across actual HTTP outages."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx
import pytest

from aheaddb.live.client import Client
from aheaddb.live.store import Unavailable


def test_real_http_outage_then_recovery():
    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_GET(self):
            self.__class__.calls += 1
            if self.calls == 1:
                self.send_response(503)
                body = b"service restarting"
            else:
                self.send_response(200)
                body = json.dumps({"source_id": "synthetic", "revision": 2,
                                   "processed_revision": 2, "clock_unknown": False,
                                   "purge_epoch": 0, "views": [], "jobs": []}).encode()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        with Client(f"http://127.0.0.1:{server.server_port}") as client:
            with pytest.raises(Unavailable, match="coverage is unknown"):
                client.status()
            assert client.status()["revision"] == 2
            assert Handler.calls == 2
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("body", [b"", b"{", b"[]", b"null", b'{"detail":null}', b'{"detail":42}'])
def test_nonstandard_503_uses_generic_unavailable(body):
    transport = httpx.MockTransport(lambda request: httpx.Response(503, content=body))
    with Client(transport=transport) as client, pytest.raises(Unavailable, match="coverage is unknown"):
        client.status()


def test_structured_detail_and_other_http_errors_remain_distinct():
    responses = iter([httpx.Response(503, json={"detail": "Backlog"}), httpx.Response(409)])
    transport = httpx.MockTransport(lambda request: next(responses))
    with Client(transport=transport) as client:
        with pytest.raises(Unavailable, match="Backlog"):
            client.status()
        with pytest.raises(httpx.HTTPStatusError) as error:
            client.status()
        assert error.value.response.status_code == 409

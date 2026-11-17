"""External-style application: uses only the installed public HTTP SDK.

Run the API and a worker separately, then: python examples/live_desk/app.py
No database connection, private paths or Engine imports exist in this application.
"""
from __future__ import annotations

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from aheaddb.live import Edit, Mutation, Unavailable
from aheaddb.live.client import Client


class App(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Customer identifiers and edits do not belong in access logs.

    def reply(self, code, data, content_type="application/json"):
        payload = data if isinstance(data, bytes) else json.dumps(data, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def valid_origin(self):
        host = self.headers.get("Host")
        return host in {"127.0.0.1:8791", "localhost:8791"} and self.headers.get("Origin", f"http://{host}") == f"http://{host}"

    def do_GET(self):
        if not self.valid_origin():
            return self.reply(403, {"error": "Local origin required"})
        path = urlsplit(self.path)
        if path.path == "/":
            return self.reply(200, Path(__file__).with_name("index.html").read_bytes(), "text/html; charset=utf-8")
        if path.path == "/features":
            allow_stale = parse_qs(path.query).get("allow_stale") == ["true"]
            try:
                with Client() as client:
                    group = client.read_group(("return_14d", "revenue_14d"),
                                              max_age_seconds=60, allow_stale=allow_stale)
                    answers = [group.answers["return_14d"], group.answers["revenue_14d"]]
                # The group carries one source observation; each member's
                # freshness includes its definition epoch at that observation.
                covers_head = all(answer.coverage.state == "fresh" for answer in answers)
                if not covers_head and not allow_stale:
                    raise Unavailable("The group does not cover the current source and view definitions")
                status = {"source_id": answers[0].coverage.source_id,
                          "revision": answers[0].coverage.current_revision,
                          "clock_unknown": any(answer.coverage.state == "unknown" for answer in answers)}
                return self.reply(200, {"features": [a.model_dump(mode="json") for a in answers], "status": status,
                                       "covers_observed_head": covers_head, "same_source_boundary": True})
            except Unavailable:
                return self.reply(503, {"error": "Current estimates are unavailable. A refresh may be pending or the service may be offline."})
            except Exception:
                return self.reply(503, {"error": "Prediction service unavailable. Check the local API and worker."})
        return self.reply(404, {"error": "Unknown route"})

    def do_POST(self):
        if not self.valid_origin():
            return self.reply(403, {"error": "Local origin required"})
        if self.path != "/orders":
            return self.reply(404, {"error": "Unknown route"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Invalid body size")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or set(body) != {"request_id", "entity", "amount"}:
                raise ValueError("Expected a complete saved order")
            request_id = body["request_id"]
            if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
                raise ValueError("Expected a canonical request UUID")
            if not isinstance(body.get("entity"), str) or not 0 < len(body["entity"]) <= 128:
                raise ValueError("Choose a customer")
            # Explicit historical synthetic fixture: these are source edits today,
            # not a claim that the forecast information cutoff advances to today.
            order = "LIVE-" + request_id
            amount = float(body.get("amount", 450))
            if not 0 < amount <= 10000:
                raise ValueError("Amount must be in (0,10000]")
            edit = Edit(request_id=request_id, mutations=(Mutation(table="orders", key=order, row={
                "id": order, "entity_id": body["entity"], "occurred_at": "2024-07-14T12:00:00Z",
                "available_at": "2024-07-14T13:00:00Z", "amount": amount}),))
            with Client() as client:
                revision = client.edit(edit)
            return self.reply(200, {"revision": revision, "order": order, "request_id": request_id})
        except ValueError:
            return self.reply(422, {"error": "Choose a customer and a valid amount."})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {409, 422}:
                return self.reply(exc.response.status_code, {"error": "Saved order conflicts with an existing request." if exc.response.status_code == 409
                                                           else "Order was rejected. Correct the customer or amount."})
            return self.reply(503, {"error": "Order was not confirmed. Retry the saved order to recover its receipt."})
        except Exception:
            return self.reply(503, {"error": "Order was not confirmed. Retry the saved order to recover its receipt."})


if __name__ == "__main__":
    print("SignalDesk Live: http://127.0.0.1:8791 (synthetic local shop)")
    ThreadingHTTPServer(("127.0.0.1", 8791), App).serve_forever()

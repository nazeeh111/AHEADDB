"""Local-only versioned API; one explicit owned Store, no file/DSN endpoints."""
from __future__ import annotations

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .contracts import (
    Answer,
    Bootstrap,
    Edit,
    EditReceipt,
    ErrorResponse,
    EvaluationReport,
    EvaluationRequest,
    ModelReceipt,
    ModelRecipe,
    Status,
    ViewDefinition,
    ViewReceipt,
)
from .store import Conflict, Store, Unavailable, decode_snapshot


def create_live_app(store: Store) -> FastAPI:
    app = FastAPI(title="AHEADDB Live", version="1.0.0", description="Owned-source predictive view contract v1",
                  responses={code: {"model": ErrorResponse} for code in (403, 409, 413, 422, 503)})
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"api_version": "v1", "code": "origin_rejected"}, status_code=403)
        if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", "0")) > 1_000_000:
            return JSONResponse({"api_version": "v1", "code": "body_too_large"}, status_code=413)
        if request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 1_000_000:
                    return JSONResponse({"api_version": "v1", "code": "body_too_large"}, status_code=413)
                body.extend(chunk)
            # Starlette's cached request forwards this bounded body downstream.
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(Unavailable)
    async def unavailable(request, exc):
        return JSONResponse({"api_version": "v1", "code": "unavailable", "detail": str(exc)}, status_code=503)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"api_version": "v1", "code": "conflict", "detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        # Validators may contain submitted private row data. Keep errors structural.
        return JSONResponse({"api_version": "v1", "code": "invalid_request"}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"api_version": "v1", "code": "invalid_request"}, status_code=422)

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        return JSONResponse({"api_version": "v1", "code": "source_coverage_unknown"}, status_code=503)

    @app.exception_handler(Exception)
    async def service_error(request, exc):
        return JSONResponse({"api_version": "v1", "code": "service_unavailable"}, status_code=503)

    @app.get("/v1/status", response_model=Status)
    def status():
        return store.status()

    @app.get("/v1/views/{name}", response_model=Answer)
    def read(name: str, generation: str | None = None,
             max_age_seconds: float | None = Query(None, gt=0, le=86400), allow_stale: bool = False):
        return store.read(name, generation=generation, max_age_seconds=max_age_seconds, allow_stale=allow_stale)

    @app.post("/v1/source/bootstrap", response_model=EditReceipt)
    def bootstrap(body: Bootstrap):
        snapshot = decode_snapshot(body.model_dump(mode="json", by_alias=True))
        return {"api_version": "v1", "revision": store.bootstrap(snapshot)}

    @app.post("/v1/evaluations", response_model=EvaluationReport)
    def evaluate(body: EvaluationRequest):
        return store.evaluate(body)

    @app.post("/v1/source/edits", response_model=EditReceipt)
    def edit(body: Edit):
        return {"api_version": "v1", "revision": store.edit(body)}

    @app.post("/v1/models", response_model=ModelReceipt)
    def model(body: ModelRecipe):
        return {"api_version": "v1", "model_id": store.register_model(body)}

    @app.post("/v1/views", response_model=ViewReceipt)
    def view(body: ViewDefinition):
        return {"api_version": "v1", "view_name": store.publish_view(body)}

    return app

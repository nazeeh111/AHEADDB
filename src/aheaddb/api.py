"""Local application/API. No arbitrary query or source-path endpoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .compiler import Compiler
from .project import Project
from .spec import StrictModel


class Change(StrictModel):
    entity_id: str = Field(min_length=1, max_length=128)


def create_app(project: Project) -> FastAPI:
    app = FastAPI(title="AHEADDB", version="0.1.0", description="Local maintained predictive views")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin and origin != expected:
            return JSONResponse({"detail": "Cross-origin access is disabled"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        return response

    @app.exception_handler(KeyError)
    async def unknown(request: Request, exc: KeyError):
        return JSONResponse({"detail": "Unknown task or entity"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/api/state")
    def state():
        with project.lock:
            return {"tasks": [t.model_dump() for t in project.tasks.values()],
                    "snapshot": project.engine.snapshot.inspect(), "refresh": project.engine.last_refresh,
                    "synthetic": project.config.synthetic_demo}

    @app.get("/api/predictions/{task}")
    def predictions(task: str):
        return project.predictions(task)

    @app.get("/api/plan/{task}")
    def plan(task: str):
        return project.plan(task)

    @app.get("/api/backtest/{task}")
    def backtest(task: str):
        return project.backtest(task)

    @app.get("/api/provenance/{task}/{entity_id}")
    def provenance(task: str, entity_id: str):
        with project.lock:
            view = project.engine.views[task]
            if entity_id not in view.predictions.index:
                raise HTTPException(404, "Unknown entity")
            return Compiler(project.engine.snapshot).provenance(project.tasks[task], view.cutoff, entity_id)

    @app.post("/api/refresh")
    def refresh():
        return project.refresh()

    @app.post("/api/demo/change")
    def change(body: Change):
        return project.demo_change(body.entity_id)

    return app

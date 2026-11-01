"""Live commands import the optional PostgreSQL extra only when invoked."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated, Literal

import typer

from .project import json_text

app = typer.Typer(no_args_is_help=True, help="Durable owned-source predictive views (install aheaddb[live]).")


def _store(namespace):
    from .live import Store
    dsn = os.environ.get("AHEAD_LIVE_DSN")
    if not dsn:
        raise typer.BadParameter("Set AHEAD_LIVE_DSN to your dedicated owned PostgreSQL database")
    return Store(dsn, namespace)


@app.command()
def init(namespace: str = "ahead_live"):
    """Create/version-check only the dedicated Live schema."""
    _store(namespace).migrate()
    typer.echo('{"schema_version":1}')


@app.command()
def demo(namespace: str = "ahead_live", entities: Annotated[int, typer.Option(min=24, max=2000)] = 40):
    """Bootstrap fresh synthetic owned rows, two recipes and two view definitions."""
    from .demo import demo_schema, demo_tables, demo_tasks
    from .live import ModelRecipe, ViewDefinition
    from .snapshot import Snapshot
    store = _store(namespace)
    store.migrate()
    store.bootstrap(Snapshot(demo_tables(entities=entities), demo_schema()))
    for task in demo_tasks():
        model = store.register_model(ModelRecipe(task=task, training_cutoffs=("2024-03-01", "2024-04-01", "2024-05-01"),
                                                 training_as_of="2024-07-01", backend="persistence"))
        store.publish_view(ViewDefinition(model_id=model, cutoff="2024-07-15"))
    typer.echo(json_text(store.status()))


@app.command()
def worker(namespace: str = "ahead_live", policy: Literal["full", "fifo", "debounce", "budget"] = "budget",
           once: bool = False):
    """Run one bounded worker, reclaiming expired leases. No daemon installation."""
    from .live import Worker
    worker = Worker(_store(namespace), policy)
    while True:
        try:
            receipt = worker.once()
            if receipt:
                typer.echo(json_text(receipt))
        except Exception:
            typer.echo("Refresh failed; inspect private local exception, durable work retained", err=True)
            if once:
                raise typer.Exit(1) from None
            time.sleep(1)
        if once:
            return
        time.sleep(0.05)


@app.command()
def serve(namespace: str = "ahead_live", port: int = 8790):
    """Serve the local v1 HTTP contract; run a worker in another terminal."""
    import uvicorn

    from .live.api import create_live_app
    uvicorn.run(create_live_app(_store(namespace)), host="127.0.0.1", port=port, access_log=False)


@app.command()
def model(recipe: Path, namespace: str = "ahead_live"):
    """Register a JSON training recipe (never load executable model bytes)."""
    from .live import ModelRecipe
    typer.echo(json_text({"model_id": _store(namespace).register_model(ModelRecipe.model_validate_json(recipe.read_text()))}))


@app.command()
def view(definition: Path, namespace: str = "ahead_live"):
    from .live import ViewDefinition
    typer.echo(json_text({"view_name": _store(namespace).publish_view(ViewDefinition.model_validate_json(definition.read_text()))}))


@app.command()
def edit(transaction: Path, namespace: str = "ahead_live"):
    from .live import Edit
    typer.echo(json_text({"revision": _store(namespace).edit(Edit.model_validate_json(transaction.read_text()))}))


@app.command()
def read(name: str, namespace: str = "ahead_live", generation: str | None = None,
         max_age_seconds: float | None = None, allow_stale: bool = False):
    answer = _store(namespace).read(name, generation=generation, max_age_seconds=max_age_seconds, allow_stale=allow_stale)
    typer.echo(answer.model_dump_json(indent=2))


@app.command()
def status(namespace: str = "ahead_live"):
    typer.echo(json_text(_store(namespace).status()))


@app.command()
def cancel(name: str, namespace: str = "ahead_live"):
    _store(namespace).cancel(name)


@app.command()
def retry(name: str, namespace: str = "ahead_live"):
    _store(namespace).retry(name)


@app.command()
def retain(namespace: str = "ahead_live", per_view: int = 5):
    typer.echo(json_text(_store(namespace).retain(per_view)))


@app.command()
def acknowledge_clock(namespace: str = "ahead_live"):
    """Assert clock is stable; invalidate existing publication pointers and refresh."""
    _store(namespace).acknowledge_clock()


@app.command()
def backup(destination: Path, ledger: Path, namespace: str = "ahead_live"):
    """Save a private data-only backup. Supply the current external purge ledger."""
    from .live.maintenance import backup
    typer.echo(json_text({"sha256": backup(_store(namespace), destination, ledger)}))


@app.command()
def restore(source: Path, ledger: Path, namespace: Annotated[str, typer.Option()]):
    """Restore into a fresh namespace, replaying current purge intent first."""
    from .live.maintenance import restore
    restore(_store(namespace), source, ledger)
    typer.echo('{"restored":true,"clock_acknowledgement_required":true}')


@app.command()
def purge(entity: str, ledger: Path, namespace: str = "ahead_live"):
    """Erase an entity and all derived artifacts; preserve intent outside backups."""
    from .live.maintenance import purge
    typer.echo(json_text({"revision": purge(_store(namespace), entity, ledger)}))


@app.command()
def bootstrap(source: Path, namespace: str = "ahead_live"):
    """Create the declared owned source once from a data-only JSON bootstrap file."""
    from .live.contracts import Bootstrap
    from .live.store import decode_snapshot
    document = Bootstrap.model_validate_json(source.read_text())
    store = _store(namespace)
    store.migrate()
    revision = store.bootstrap(decode_snapshot(document.model_dump(mode="json", by_alias=True)))
    typer.echo(json_text({"api_version": "v1", "revision": revision}))


@app.command()
def evaluate(request: Path, namespace: str = "ahead_live"):
    """Chronological evaluation against prior, historical and tabular models."""
    from .live.contracts import EvaluationRequest
    report = _store(namespace).evaluate(EvaluationRequest.model_validate_json(request.read_text()))
    typer.echo(report.model_dump_json(indent=2))

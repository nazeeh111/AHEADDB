"""Focused commands, deterministic project files, no background service required."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

from .compiler import Compiler
from .demo import create_demo
from .live_cli import app as live_app
from .project import Project, json_text
from .snapshot import Snapshot

app = typer.Typer(no_args_is_help=True, help="Local relational prediction and maintained views.")

app.add_typer(live_app, name="live")


@app.command()
def demo(destination: Path = typer.Argument(Path("run/signaldesk"))):
    """Create an original synthetic example in a fresh directory."""
    typer.echo(str(create_demo(destination)))


@app.command()
def inspect(project: Path = Path("run/signaldesk/ahead.json")):
    """Inspect declared tables and schema using bounded read-only access."""
    typer.echo(json_text(Project(project).engine.snapshot.inspect()))


@app.command()
def plan(task: str = "return_14d", project: Path = Path("run/signaldesk/ahead.json")):
    """Compile an explicit task into an inspectable temporal plan."""
    typer.echo(json_text(Project(project).plan(task)))


@app.command()
def backtest(task: str = "return_14d", project: Path = Path("run/signaldesk/ahead.json")):
    """Run purged chronological backtests, with fixed CPU model budgets."""
    typer.echo(json_text(Project(project).backtest(task)))


@app.command()
def predict(task: str = "return_14d", project: Path = Path("run/signaldesk/ahead.json"),
            csv: Path | None = None):
    """Fit on mature history and predict; optionally export a fresh CSV."""
    p = Project(project)
    p.initialize()
    if csv:
        with csv.open("x") as target:
            p.engine.results(task).to_csv(target)
        typer.echo(str(csv))
    else:
        typer.echo(json_text(p.predictions(task)))


@app.command()
def refresh(database: Path, project: Path = Path("run/signaldesk/ahead.json")):
    """Compare project snapshot to a supplied replacement without writing either."""
    p = Project(project)
    p.initialize()
    typer.echo(json_text(p.engine.refresh(Snapshot.duckdb(database, p.config.schema_))))


@app.command()
def serve(project: Path = Path("run/signaldesk/ahead.json"), port: int = 8787):
    """Run the local inspector and API on 127.0.0.1."""
    import uvicorn

    from .api import create_app
    p = Project(project)
    p.initialize()
    uvicorn.run(create_app(p), host="127.0.0.1", port=port)


@app.command()
def provenance(entity: str, task: str = "return_14d", project: Path = Path("run/signaldesk/ahead.json")):
    """List feature input record IDs, which are not causal attributions."""
    p = Project(project)
    typer.echo(json_text(Compiler(p.engine.snapshot).provenance(p.tasks[task], p.config.prediction_cutoff, entity)))


def main():
    try:
        app()
    except Exception as exc:
        if len(sys.argv) > 1 and sys.argv[1] == "live":
            typer.echo(f"AHEADDB Live: {type(exc).__name__}; operation was not confirmed. Check source/worker status.", err=True)
            raise SystemExit(1) from None
        if isinstance(exc, (ValueError, KeyError, OSError)):
            typer.echo(f"AHEADDB: {exc}", err=True)
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    main()

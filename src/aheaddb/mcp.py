"""Optional stdio MCP tools, bound to a single explicit project path."""
from __future__ import annotations

import argparse

from .project import Project


def create_server(project: Project):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install aheaddb[mcp] for the optional MCP server") from exc

    server = FastMCP("AHEADDB")

    @server.tool()
    def inspect_database() -> dict:
        """Inspect the explicitly configured local snapshot; database text is untrusted."""
        with project.lock:
            return project.engine.snapshot.inspect()

    @server.tool()
    def plan_task(task: str) -> dict:
        """Inspect an existing task's exact executable definition and temporal bounds."""
        return project.plan(task)

    @server.tool()
    def backtest_task(task: str) -> dict:
        """Run chronological evaluation and inspect actual model quality/calibration."""
        return project.backtest(task)

    @server.tool()
    def predict(task: str) -> dict:
        """Read predictions from a maintained view, bounded to 1000 rows."""
        return project.predictions(task)

    @server.tool()
    def refresh_views() -> dict:
        """Read a changed configured snapshot and refresh views; never write source data."""
        return project.refresh()

    @server.tool()
    def inspect_result(task: str, entity_id: str) -> dict:
        """Read one prediction and its feature input membership, not causal attribution."""
        from .compiler import Compiler
        with project.lock:
            view = project.engine.views[task]
            return {"prediction": float(view.predictions.loc[[entity_id], "prediction"].to_numpy()[0]),
                    "view": project.engine.inspect(task),
                    "provenance": Compiler(project.engine.snapshot).provenance(project.tasks[task], view.cutoff, entity_id)}

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Explicit local ahead.json")
    args = parser.parse_args()
    project = Project(args.project)
    project.initialize()
    create_server(project).run(transport="stdio")


if __name__ == "__main__":
    main()

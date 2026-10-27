"""Actual HTTP, CLI, readonly persistence and stdio protocol integration."""
from __future__ import annotations

import asyncio
import os
import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aheaddb import Compiler, Snapshot
from aheaddb.api import create_app
from aheaddb.cli import app
from aheaddb.demo import create_demo
from aheaddb.project import Project
from aheaddb.spec import digest


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    return Project(create_demo(tmp_path_factory.mktemp("signaldesk")))


def test_http_predict_change_refresh_and_provenance(project):
    project.initialize()
    original_bytes = project.database.read_bytes()
    client = TestClient(create_app(project))
    assert client.get("/").status_code == 200
    assert client.get("/api/state").json()["synthetic"] is True
    assert client.get("/api/plan/return_14d").json()["label_matures_at"]
    records = client.get("/api/predictions/return_14d").json()["predictions"]
    entity = records[0]["entity_id"]
    provenance = client.get(f"/api/provenance/return_14d/{entity}").json()
    assert "not causal" in provenance["meaning"]
    result = client.post("/api/demo/change", json={"entity_id": entity}).json()
    assert result["refresh"]["prediction_rows"] == 2
    assert result["refresh"]["reused_prediction_rows"] == 318
    assert project.database.read_bytes() == original_bytes
    reference = Compiler(project.engine.snapshot).features(project.tasks["return_14d"], project.config.prediction_cutoff)
    pd.testing.assert_series_equal(project.engine.results("return_14d").prediction,
                                  pd.Series(project.engine.views["return_14d"].model.predict(reference),
                                            index=reference.index, name="prediction"))
    assert client.post("/api/refresh").status_code == 200
    assert client.get("/api/predictions/unknown").status_code == 404
    assert client.post("/api/demo/change", json={"entity_id": "absent"}).status_code == 422
    assert client.get("/api/state", headers={"host": "evil.example"}).status_code == 400
    assert client.post("/api/refresh", headers={"origin": "https://evil.example"}).status_code == 403
    assert project.database.read_bytes() == original_bytes


def test_readonly_cli_and_snapshot_copy(project, tmp_path):
    runner = CliRunner()
    for args in [["inspect"], ["plan"], ["predict"], ["provenance", "C0000"]]:
        result = runner.invoke(app, [*args, "--project", str(project.path)])
        assert result.exit_code == 0, result.output
    csv = tmp_path / "predictions.csv"
    assert runner.invoke(app, ["predict", "--project", str(project.path), "--csv", str(csv)]).exit_code == 0
    assert len(pd.read_csv(csv)) == 160
    assert runner.invoke(app, ["predict", "--project", str(project.path), "--csv", str(csv)]).exit_code != 0
    before = project.engine.snapshot.version
    copy = project.engine.snapshot.table("orders")
    copy["amount"] = -999
    assert project.engine.snapshot.version == before
    fresh = Snapshot.duckdb(project.database, project.config.schema_)
    assert fresh.version == project.engine.snapshot.version
    assert digest(project.database.read_bytes().hex())


def test_mcp_stdio_roundtrip(project):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable,
                                      args=["-m", "aheaddb.mcp", "--project", str(project.path)],
                                      env={**os.environ, "OMP_NUM_THREADS": "1", "LOKY_MAX_CPU_COUNT": "1"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {"inspect_database", "plan_task", "backtest_task",
                                                       "predict", "refresh_views", "inspect_result"}
                for name, args in [("inspect_database", {}), ("plan_task", {"task": "return_14d"}),
                                   ("predict", {"task": "return_14d"}),
                                   ("inspect_result", {"task": "return_14d", "entity_id": "C0000"}),
                                   ("refresh_views", {})]:
                    result = await session.call_tool(name, args)
                    assert not result.isError, result.content
    asyncio.run(run())


def test_visible_insufficient_history_and_feature_range(project):
    view = project.engine.views["return_14d"]
    x = Compiler(project.engine.snapshot).features(view.task, view.cutoff)
    x[:] = 0
    x.iloc[0, 0] = 1000000
    report = view.model.diagnostics(x)
    assert report["rows_outside_training_range"] > 0
    assert report["rows_without_history"] == len(x) - 1


def test_parquet_matches_readonly_duckdb_snapshot(project, tmp_path):
    import duckdb
    tables = {}
    with duckdb.connect() as conn:
        for name in [project.config.schema_.entity_table, *(e.table for e in project.config.schema_.events)]:
            target = tmp_path / f"{name}.parquet"
            conn.from_df(project.engine.snapshot.table(name)).write_parquet(str(target))
            tables[name] = target
    restored = Snapshot.parquet(tables, project.config.schema_)
    assert restored.version == project.engine.snapshot.version
    with pytest.raises(ValueError, match="row limit"):
        Snapshot.parquet(tables, project.config.schema_, max_rows=1)

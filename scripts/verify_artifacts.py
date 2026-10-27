"""Recompute published metrics and verify comparators use identical held-out labels."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from aheaddb.modeling import metrics

ROOT = Path(__file__).resolve().parents[1]


def verify() -> dict:
    checks = []
    for dataset in ["retail", "beijing"]:
        core = json.loads((ROOT / f"artifacts/results/public-{dataset}.json").read_text())
        other = json.loads((ROOT / f"artifacts/results/fastdfs-{dataset}.json").read_text())
        assert core["data_version"] == other["data_version"]
        csvs = [(pd.read_csv(ROOT / f"artifacts/results/public-{dataset}-predictions.csv"), core),
                (pd.read_csv(ROOT / f"artifacts/results/fastdfs-{dataset}-predictions.csv"), other)]
        reference = {}
        for df, report in csvs:
            for run in report["results"]:
                task = run["task"]
                rows = df.loc[(df.task == task["name"]) & (df.backend == run["backend"])].copy()
                assert len(rows) == run["test_samples"]
                assert np.isfinite(rows[["actual", "prediction"]].to_numpy()).all()
                if task["kind"] == "binary":
                    assert rows.prediction.between(0, 1).all()
                recalculated = metrics(rows.actual.to_numpy(), rows.prediction.to_numpy(), task["kind"])
                for k, value in recalculated.items():
                    if isinstance(value, (float, int)):
                        assert np.isclose(value, run["metrics"][k], rtol=1e-12, atol=1e-12), (dataset, k)
                index = rows.set_index(["fold", "cutoff", "entity_id"]).actual.sort_index()
                assert index.index.is_unique
                if task["name"] in reference:
                    pd.testing.assert_series_equal(reference[task["name"]], index)
                else:
                    reference[task["name"]] = index
                checks.append({"dataset": dataset, "task": task["name"], "backend": run["backend"],
                               "rows": len(rows), "metrics_recomputed": True, "aligned_labels": True})
    refresh = json.loads((ROOT / "artifacts/results/refresh.json").read_text())
    assert len(refresh["records"]) == 90
    assert max(r["max_absolute_error"] for r in refresh["records"]) == 0
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT / "src/aheaddb").rglob("*.py"))}
    return {"checks": checks, "refresh_cases": len(refresh["records"]), "max_refresh_error": 0,
            "core_source_sha256": sources}


if __name__ == "__main__":
    result = verify()
    dest = ROOT / "artifacts/validation/artifacts.json"
    dest.write_text(json.dumps(result, indent=2) + "\n")
    print(dest)

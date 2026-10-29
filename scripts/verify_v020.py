"""Independently recompute saved metrics, label alignment and refresh assertions."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    mean_squared_error,
    roc_auc_score,
)

ROOT = Path(__file__).resolve().parents[1]


def verify():
    checks = []
    for prefix in ["v020-quality", "v020-confirmation"]:
        for dataset in ["retail", "beijing"]:
            report = json.loads((ROOT / f"artifacts/results/{prefix}-{dataset}.json").read_text())
            assert report["complete"] and not report["failures"]
            frame = pd.read_csv(
                ROOT / f"artifacts/results/{prefix}-{dataset}-predictions.csv", dtype={"entity_id": str}
            )
            references = {}
            for run in report["results"]:
                assert run["error"] is None
                rows = frame.loc[
                    (frame.task == run["task"]["name"])
                    & (frame.fold == run["fold"])
                    & (frame.backend == run["backend"])
                ]
                assert len(rows) == run["test_samples"]
                actual, pred = rows.actual.to_numpy(), rows.prediction.to_numpy()
                assert np.isfinite(actual).all() and np.isfinite(pred).all()
                if run["task"]["kind"] == "binary":
                    assert np.all((pred >= 0) & (pred <= 1))
                    values = {
                        "roc_auc": roc_auc_score(actual, pred),
                        "average_precision": average_precision_score(actual, pred),
                        "brier": brier_score_loss(actual, pred),
                    }
                else:
                    values = {
                        "mae": mean_absolute_error(actual, pred),
                        "rmse": np.sqrt(mean_squared_error(actual, pred)),
                    }
                for metric, value in values.items():
                    assert np.isclose(value, run["metrics"][metric], rtol=1e-12, atol=1e-12), (
                        prefix,
                        dataset,
                        metric,
                    )
                index = rows.set_index(["cutoff", "entity_id"]).actual.sort_index()
                assert index.index.is_unique
                key = (run["task"]["name"], run["fold"])
                if key in references:
                    pd.testing.assert_series_equal(index, references[key])
                else:
                    references[key] = index
                checks.append(
                    {
                        "prefix": prefix,
                        "dataset": dataset,
                        "task": run["task"]["name"],
                        "fold": run["fold"],
                        "backend": run["backend"],
                        "rows": len(rows),
                    }
                )
    bench = json.loads((ROOT / "artifacts/results/v020-refresh.json").read_text())
    comparisons = sum(len(w["records"]) for w in bench["workloads"])
    assert comparisons == 770
    assert all(x["max_absolute_error"] == 0 for w in bench["workloads"] for x in w["records"])
    return {
        "quality_comparisons": len(checks),
        "checks": checks,
        "refresh_comparisons": comparisons,
        "max_refresh_error": 0,
    }


if __name__ == "__main__":
    result = verify()
    p = ROOT / "artifacts/validation/v020-artifacts.json"
    p.write_text(json.dumps(result, indent=2) + "\n")
    print(p)

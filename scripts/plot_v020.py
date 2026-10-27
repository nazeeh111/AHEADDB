"""Plot saved v0.2 quality/performance evidence without running models."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "aheaddb-matplotlib"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main(dest):
    dest.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "#f5f3ed",
            "axes.facecolor": "#f5f3ed",
            "svg.hashsalt": "aheaddb-v020",
        }
    )
    summary = json.loads((ROOT / "artifacts/results/v020-confirmation-summary.json").read_text())
    fresh = {r["task"]: r for r in summary if r["evidence"] == "newly_fixed_temporal_confirmation"}
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.6), layout="constrained")
    for ax, (task, metric, title) in zip(
        axes,
        [
            ("pm25_over_75_next_day", "roc_auc", "ROC AUC: higher is better"),
            ("pm25_over_75_next_day", "brier", "Brier: lower is better"),
            ("pm25_next_day", "mae", "MAE: lower is better"),
        ],
        strict=True,
    ):
        values = fresh[task]["metrics"][metric]
        bars = ax.bar(
            ["v0.1.0", "v0.2.0"],
            [values["legacy"], values["adaptive"]],
            color=["#778d85", "#a13e22"],
            width=0.55,
        )
        ax.set_title(title, loc="left", fontsize=11, pad=15)
        ax.set_ylim(0, 1 if metric == "roc_auc" else max(values["legacy"], values["adaptive"]) * 1.3)
        ax.bar_label(bars, fmt="%.3f" if metric != "mae" else "%.2f", padding=5)
        ax.grid(axis="y", alpha=0.15)
        ax.set_axisbelow(True)
    axes[2].set_ylabel("Daily PM2.5 mean units")
    fig.suptitle(
        "Fixed February confirmation: better in this small period",
        x=0.015,
        ha="left",
        weight="bold",
        fontsize=16,
    )
    fig.supxlabel(
        "96 station-day labels per task across eight dates; dependent samples and complete-case selection\nPrespecified February check after disclosed development; no universal accuracy or calibration claim",
        fontsize=9,
    )
    for ext in ["png", "svg"]:
        fig.savefig(dest / f"v020-quality.{ext}", dpi=160, bbox_inches="tight")
    plt.close(fig)
    r = json.loads((ROOT / "artifacts/results/v020-refresh.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), layout="constrained", sharey=True)
    scenarios = ["insertion", "value_update", "foreign_key_move", "deletion", "dense_update"]
    for ax, w in zip(axes, r["workloads"], strict=True):
        x = np.arange(len(scenarios))
        width = 0.25
        for i, (version, mode, label, color) in enumerate(
            [
                ("v010", "incremental", "v0.1 incremental", "#778d85"),
                ("v020", "incremental", "v0.2 incremental", "#a13e22"),
                ("v020", "full", "v0.2 full", "#182d3b"),
            ]
        ):
            rows = [
                next(
                    q
                    for q in w["summaries"]
                    if q["version"] == version and q["mode"] == mode and q["scenario"] == s
                )
                for s in scenarios
            ]
            y = np.array([q["wall_median_seconds"] * 1000 for q in rows])
            lo = y - np.array([q["wall_p10_seconds"] * 1000 for q in rows])
            hi = np.array([q["wall_p90_seconds"] * 1000 for q in rows]) - y
            ax.bar(
                x + (i - 1) * width,
                y,
                width,
                label=label,
                color=color,
                yerr=[lo, hi],
                capsize=2,
                error_kw={"lw": 0.8},
            )
        ax.set_xticks(x, [s.replace("_", "\n") for s in scenarios], fontsize=8)
        ax.set_title(f"{w['entities']:,} entities", loc="left")
        ax.grid(axis="y", alpha=0.15)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("Refresh wall time (ms, median; bars p10 to p90)")
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle(
        "Paired local refresh: faster than v0.1, dense updates still cost more than full",
        x=0.015,
        ha="left",
        weight="bold",
        fontsize=14,
    )
    fig.supxlabel(
        "Seven randomized interleaved repeats; actual version-specific features/models; each matches its own full reference\nSnapshot validation/index construction excluded here and reported separately with memory overhead",
        fontsize=9,
    )
    for ext in ["png", "svg"]:
        fig.savefig(dest / f"v020-refresh.{ext}", dpi=160, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dest", type=Path, default=ROOT / "assets")
    main(p.parse_args().dest)

"""Plot saved measured refresh data; never synthesize numerical results."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    report = json.loads((ROOT / "artifacts/results/refresh.json").read_text())
    records = report["records"]
    names = list(dict.fromkeys(r["scenario"] for r in records))
    colors = {"full": "#182d3b", "cache": "#778d85", "incremental": "#a13e22"}
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "figure.facecolor": "#f5f3ed", "axes.facecolor": "#f5f3ed"})
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    x = np.arange(len(names))
    for mode, color in colors.items():
        groups = [[r for r in records if r["mode"] == mode and r["scenario"] == n] for n in names]
        latency = [np.median([r["seconds"] for r in g]) * 1000 for g in groups]
        work = [np.median([r["prediction_rows"] for r in g]) for g in groups]
        axes[0].plot(x, latency, "o-", color=color, label=mode, linewidth=1.7)
        axes[1].plot(x, work, "o-", color=color, label=mode, linewidth=1.7)
    axes[0].set_ylabel("Refresh latency (ms, median)")
    axes[1].set_ylabel("Prediction rows recomputed")
    axes[1].set_xticks(x, [n.replace("_", "\n") for n in names], fontsize=8)
    for ax in axes:
        ax.grid(axis="y", alpha=.2)
    axes[0].legend(frameon=False, ncol=3)
    fig.suptitle("Selective work does not guarantee lower latency", x=.08, ha="left", fontsize=17, weight="bold")
    fig.text(.08, .9, f"Synthetic shop · {report['entities']} entities · two tasks · three repeats · source diff included; snapshot loading excluded", fontsize=10)
    fig.text(.08, .015, "Full and ordinary cache skip incremental diff scans. Clock and dense updates expose worst cases. See saved records and limits.", fontsize=9)
    fig.tight_layout(rect=(0.02, .04, 1, .88))
    for ext in ["svg", "png"]:
        fig.savefig(ROOT / f"assets/refresh.{ext}", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()

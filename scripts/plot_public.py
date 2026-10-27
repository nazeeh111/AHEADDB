"""Plot saved public-data metrics; no training, invented values, or raw data needed."""
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

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "artifacts" / "results"
DEST = ROOT / "assets"
NAMES = {"historical": "Historical", "baseline": "Training prior / mean", "histogram": "Histogram boosting",
         "fastdfs-extratrees": "FastDFS + ExtraTrees"}
COLORS = {"historical": "#889299", "baseline": "#c7b89a", "histogram": "#0b6a59", "fastdfs-extratrees": "#b66d19"}


def load(dataset: str) -> list[dict]:
    rows = json.loads((RESULTS / f"public-{dataset}.json").read_text())["results"]
    optional = RESULTS / f"fastdfs-{dataset}.json"
    if optional.exists():
        rows.extend(json.loads(optional.read_text())["results"])
    return rows


def save(fig: plt.Figure, name: str) -> None:
    dest = DEST
    dest.mkdir(exist_ok=True)
    for suffix in ["svg", "png"]:
        fig.savefig(dest / f"{name}.{suffix}", dpi=170, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.edgecolor": "#d3d8d3", "text.color": "#24312d",
                         "axes.labelcolor": "#24312d", "xtick.color": "#52605a", "ytick.color": "#24312d",
                         "svg.hashsalt": "aheaddb-public-v1"})
    data = {name: load(name) for name in ["retail", "beijing"]}
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.6), layout="constrained", facecolor="#fbfbf7")
    for ax, (dataset, task_name, title, metric, unit) in zip(axes.flat, [
        ("retail", "purchase_30d", "Retail · purchase within 30 days", "roc_auc", "ROC AUC · higher is better"),
        ("retail", "gross_spend_30d", "Retail · gross spend in 30 days", "mae", "MAE · GBP · lower is better"),
        ("beijing", "pm25_over_75_next_day", "Beijing · next-day PM2.5 above 75", "roc_auc", "ROC AUC · higher is better"),
        ("beijing", "pm25_next_day", "Beijing · next-day mean PM2.5", "mae", "MAE · micrograms/m³ · lower is better"),
    ], strict=True):
        rows = [r for r in data[dataset] if r["task"]["name"] == task_name]
        values = [r["metrics"][metric] for r in rows]
        ax.set_facecolor("#fbfbf7")
        ax.barh(range(len(rows)), values, color=[COLORS[r["backend"]] for r in rows], height=0.6)
        ax.set_yticks(range(len(rows)), [NAMES[r["backend"]] for r in rows])
        ax.invert_yaxis()
        ax.set_title(f"{title}\nn={rows[0]['test_samples']:,} held-out entity/cutoff samples", loc="left", fontsize=11, pad=16)
        ax.set_xlabel(unit)
        limit = 1.0 if metric == "roc_auc" else max(values) * 1.25
        ax.set_xlim(0, limit)
        ax.set_axisbelow(True)
        ax.grid(axis="x", alpha=0.17)
        for i, value in enumerate(values):
            ax.text(value + limit * 0.015, i, f"{value:.3f}" if metric == "roc_auc" else f"{value:.1f}", va="center", fontsize=9)
    fig.suptitle("AHEADDB · actual public-data predictions", x=0.02, ha="left", fontsize=18, fontweight="bold")
    fig.supxlabel("Fixed chronological folds · assumptions and missing-label exclusions in docs/data.md\nNormalized station time series and retail invoices; no foundation-model or broad superiority claim", fontsize=9)
    save(fig, "public-quality")

    fig, axes = plt.subplots(1, 2, figsize=(10.7, 4.8), layout="constrained", facecolor="#fbfbf7")
    for ax, dataset in zip(axes, ["retail", "beijing"], strict=True):
        rows = [r for r in data[dataset] if r["task"]["kind"] == "binary"]
        ax.set_facecolor("#fbfbf7")
        ax.plot([0, 1], [0, 1], ":", color="#8a938f", linewidth=1, label="Perfect reliability")
        for row in rows:
            bins = row["metrics"]["reliability"]
            ax.plot([b["probability"] for b in bins], [b["frequency"] for b in bins], "o-",
                    color=COLORS[row["backend"]], markersize=4, linewidth=1.5,
                    label=f"{NAMES[row['backend']]} · Brier {row['metrics']['brier']:.3f}")
        ax.set(xlim=(0, 1), ylim=(0, 1), xlabel="Mean predicted probability in bin", ylabel="Observed outcome frequency")
        ax.set_title("Retail · 30-day purchase" if dataset == "retail" else "Beijing · next-day PM2.5 above 75", loc="left", fontsize=11)
        ax.grid(alpha=0.15)
        ax.legend(loc="upper left", fontsize=8, frameon=False)
    fig.suptitle("Raw probabilities need calibration", x=0.02, ha="left", fontsize=18, fontweight="bold")
    fig.supxlabel("Five equal-width probability bins · empty bins omitted · dependent temporal samples\nNo calibration fitted on held-out data. Saved numerical reliability bins are in artifacts/results.", fontsize=9)
    save(fig, "public-calibration")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=ROOT / "assets")
    DEST = parser.parse_args().dest
    main()

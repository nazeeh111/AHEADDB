"""Portable replay of the unchanged frozen evaluator, into a fresh output folder.

Added after confirmation solely for portable temporary storage and saved-output
verification. The original evaluator/protocol remain byte-identical and checked.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import evaluate_v020_confirmation as frozen
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", choices=["all", "retail", "beijing"], default="all")
    p.add_argument("--data-dir", type=Path, default=frozen.ROOT / ".data")
    p.add_argument("--dest", type=Path, required=True)
    args = p.parse_args()
    if args.dest.exists():
        raise ValueError("Choose a fresh replay directory to preserve previous results")
    protocol = json.loads((frozen.ROOT / "artifacts/results/v020-confirmation-protocol.json").read_text())
    expected = protocol.pop("protocol_sha256")
    if (
        frozen.canonical_hash(protocol) != expected
        or frozen.sha256(frozen.PROTOCOL_DOCUMENT) != protocol["document_sha256"]
    ):
        raise ValueError("Frozen protocol/document changed")
    protocol["protocol_sha256"] = expected
    if frozen.sha256(Path(frozen.__file__)) != protocol["evaluator_sha256"]:
        raise ValueError("Frozen evaluator changed")
    for relative, digest in protocol["first_phase_evidence_sha256"].items():
        if frozen.sha256(frozen.ROOT / relative) != digest:
            raise ValueError(f"Preserved evidence changed: {relative}")
    if frozen.sha256(frozen.ROOT / "scripts/evaluate_v020.py") != protocol["first_phase_evaluator_sha256"]:
        raise ValueError("First-stage evaluator changed")
    args.dest.mkdir(parents=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix="aheaddb-replay-") as folder:
        legacy = frozen.load_legacy(Path(folder))
        with threadpool_limits(limits=1):
            for dataset in ["retail", "beijing"] if args.dataset == "all" else [args.dataset]:
                frozen.evaluate(dataset, protocol, args.dest, args.data_dir, legacy)
                name = f"v020-confirmation-{dataset}-predictions.csv"
                observed = pd.read_csv(args.dest / name, dtype={"entity_id": str})
                reference = pd.read_csv(frozen.ROOT / "artifacts/results" / name, dtype={"entity_id": str})
                pd.testing.assert_frame_equal(observed, reference, check_exact=False, rtol=1e-12, atol=1e-12)
                checks.append(
                    {
                        "dataset": dataset,
                        "rows": len(observed),
                        "max_prediction_difference": float(
                            np.max(np.abs(observed.prediction - reference.prediction))
                        ),
                    }
                )
    (args.dest / "replay-verification.json").write_text(
        json.dumps({"protocol_sha256": expected, "checks": checks}, indent=2) + "\n"
    )
    print(args.dest / "replay-verification.json")


if __name__ == "__main__":
    main()

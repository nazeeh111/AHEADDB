"""Summarize every frozen case, preserving failures and avoiding pooled tail claims.

This reader never runs or changes a policy. Raw protocol/results remain the
experiment record. Medians below give equal weight to each repetition.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def quantile(values, q):
    ordered = sorted(values)
    point = (len(ordered) - 1) * q
    lower = math.floor(point)
    upper = math.ceil(point)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (point - lower)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("artifacts/live/confirmation-v2"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/live/verification"))
    parser.add_argument("--bundle", action="store_true", help="Bundle exact raw cases only after all cases verify")
    args = parser.parse_args()
    protocol_bytes = (args.input / "protocol.json").read_bytes()
    protocol = json.loads(protocol_bytes)
    archive = args.input / "cases.json.gz"
    archived = json.loads(gzip.decompress(archive.read_bytes())) if archive.exists() else {}
    results, missing, invalid, manifest, raw_cases = [], [], [], {}, {}
    for case, operations in protocol["cases"].items():
        for policy in protocol["policies"]:
            for repeat in range(protocol["repeats"]):
                name = f"{case}-{policy}-{repeat}.json"
                path = args.input / name
                if path.exists():
                    raw = path.read_bytes()
                elif name in archived:
                    raw = archived[name].encode()
                else:
                    missing.append(name)
                    continue
                raw_cases[name] = raw.decode()
                manifest[name] = hashlib.sha256(raw).hexdigest()
                try:
                    d = json.loads(raw)
                    assert (d["case"], d["policy"], d["repeat"]) == (case, policy, repeat)
                    assert len(d["reads"]) == protocol["read_samples"]
                    assert d["final_revision"] == d["source_commits"] + 1 == len(operations) + 1
                    assert len(d["source_to_publication"]) == len(operations) * 4
                    assert d["no_starved_task"] and d["agreement"]["independent_prediction_comparisons"] > 0
                    if case == "silence":
                        assert d["silence_proof"]["seconds"] >= protocol["silence_seconds"]
                        assert d["silence_proof"]["states"] == ["fresh"] * 4
                        assert d["silence_proof"]["new_refresh_batches"] == 0
                    results.append(d)
                except (ValueError, AssertionError, KeyError, TypeError):
                    error = path.with_name(path.stem + "-stderr.txt")
                    invalid.append({"case_file": name, "bytes": len(raw),
                                    "error_file": error.name if error.exists() else None})
    rows = []
    for case in protocol["cases"]:
        for policy in protocol["policies"]:
            group = [r for r in results if r["case"] == case and r["policy"] == policy]
            if not group:
                continue
            observations = []
            for r in group:
                states = Counter(read["state"] for read in r["reads"])
                deadlines = sum(r["deadline_misses"].values()) / len(r["source_to_publication"])
                observations.append({
                    "repeat": r["repeat"], "wall_seconds": r["wall_seconds"],
                    "setup_wall_seconds": r["setup"]["wall_seconds"],
                    "cpu_seconds": r["python_cpu_seconds"] + r["postgres_cpu_seconds"],
                    "lag_p95_seconds": quantile([x["seconds"] for x in r["source_to_publication"]], .95),
                    "read_p95_seconds": quantile([x["seconds"] for x in r["reads"]], .95),
                    "deadline_miss_fraction": deadlines,
                    "source_commits_per_second": r["source_commits"] / r["wall_seconds"],
                    "source_rows_per_second": r["source_rows_written"] / r["wall_seconds"],
                    "sample_fresh_fraction": states["fresh"] / len(r["reads"]),
                    "sample_stale_fraction": states["stale"] / len(r["reads"]),
                    "sample_unknown_fraction": states["unknown"] / len(r["reads"]),
                    "sample_unavailable_fraction": states["unavailable"] / len(r["reads"]),
                    "wal_bytes": r["database_counter_delta"]["wal_bytes"],
                    "buffer_hits": r["database_counter_delta"]["blks_hit"],
                    "blocks_read": r["database_counter_delta"]["blks_read"],
                    "python_peak_mib": r["python_peak_rss_bytes"] / 2**20,
                    "postgres_peak_sampled_mib": r["postgres_peak_sampled_footprint_bytes"] / 2**20,
                    "retained_relation_mib": r["retained_relation_bytes"] / 2**20,
                    "refresh_batches": len(r["refreshes"]),
                    "prediction_rows": sum(x["prediction_rows"] for x in r["refreshes"]),
                    "post_sample_drain_seconds": r["post_sample_drain_seconds"],
                })
            medians = {k: statistics.median(o[k] for o in observations)
                       for k in observations[0] if k != "repeat"}
            rows.append({"case": case, "policy": policy, "repetitions": len(group),
                         "median": medians, "observations": observations,
                         "deadline_misses_by_view": {
                             name: {"misses": sum(r["deadline_misses"][name] for r in group),
                                    "opportunities": sum(r["source_commits"] for r in group)}
                             for name in group[0]["deadline_misses"]}})
    complete = not missing and not invalid
    summary = {"complete": complete, "expected_cases": len(protocol["cases"]) * len(protocol["policies"]) * protocol["repeats"],
               "verified_cases": len(results), "missing_cases": missing, "invalid_cases": invalid,
               "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(), "results_sha256": manifest,
               "view_generations_checked": sum(r["agreement"]["view_generations"] for r in results),
               "unique_publication_generations_checked": sum(r["agreement"]["unique_publication_generations"] for r in results),
               "independent_prediction_comparisons": sum(r["agreement"]["independent_prediction_comparisons"] for r in results),
               "rows": rows}
    args.output.mkdir(parents=True, exist_ok=True)
    if args.bundle and complete:
        (args.output / "protocol.json").write_bytes(protocol_bytes)
        packed = json.dumps(raw_cases, sort_keys=True, ensure_ascii=False).encode()
        (args.output / "cases.json.gz").write_bytes(gzip.compress(packed, mtime=0))
    (args.output / "comparison.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    lines = ["# AHEADDB Live frozen workload results", "",
             f"Status: {'complete' if complete else 'INCOMPLETE, not a final comparison'}. "
             f"{len(results)}/{summary['expected_cases']} cases verified; {len(invalid)} invalid/unfinished files.", "",
             f"Frozen protocol SHA-256: `{summary['protocol_sha256']}`.", "",
             "Each row is the median of the reported repetitions. Latency percentiles are computed per run, then medianed; "
             "they are not pooled. The 1,000 closed-loop reads per run sample response states, not continuous uptime. "
             "Only every tenth read opts into older results. Unavailable responses combine rejected freshness and service failure; "
             "the benchmark does not classify their causes. A zero unknown sample is not proof that outage detection never fails.", "",
             "| Workload | Policy | n | CPU s | Lag p95 s | Read p95 ms | Miss % | Fresh sample % | WAL MiB | Refresh batches |", 
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        m = row["median"]
        lines.append(f"| {row['case']} | {row['policy']} | {row['repetitions']} | {m['cpu_seconds']:.2f} | "
                     f"{m['lag_p95_seconds']:.3f} | {1000*m['read_p95_seconds']:.1f} | "
                     f"{100*m['deadline_miss_fraction']:.1f} | {100*m['sample_fresh_fraction']:.1f} | "
                     f"{m['wal_bytes']/2**20:.2f} | {m['refresh_batches']:.0f} |")
    lines += ["", "## Interpretation limits", "",
              "All methods use identical owned-source persistence, HTTP transport, snapshots, leases and publication boundaries. "
              "`current` uses the original numerical Engine through the FIFO durability adapter and is algorithmically identical "
              "to `fifo`; it is a repeated control, not a separate competing algorithm. Full/FIFO already share compatible contexts. "
              "Coalescing can skip intermediate publications only when a newer committed snapshot covers the edits.", "",
              "Four views share two history contexts. Normal views have 0.4-second freshness budgets; short-history views have "
              "2-second budgets. Lag uses actual PostgreSQL source/publication commit timestamps; misses use persisted job "
              "deadlines. The JSON includes per-view opportunities/misses, setup, end-to-end throughput, source rows, sampled "
              "stale/unknown/unavailable fractions, post-sample drain, prediction work and memory/storage counters.", "",
              "CPU combines the Python service/worker/client process and sampled PostgreSQL process-tree accounting, including "
              "ingestion and persistence. WAL/buffer counters are database-wide work counters, not physical device throughput. "
              "PostgreSQL peak footprint is sampled every 50 ms and shared-memory accounting is platform dependent. "
              "Python peak RSS includes setup. Setup is reported separately; agreement verification occurs after measurement. "
              "This is one shared Mac under variable unrelated load, not an isolated capacity or production SLA test. "
              "Three repetitions do not establish statistical significance or a universal speed advantage.", "",
              "Every completed case reaches the acknowledged final revision for all views and independently checks every retained "
              "public generation against the same snapshot, cutoff and model. This demonstrates finite-workload progress, "
              "not a starvation theorem for unbounded arrivals. Synthetic stress streams validate serving mechanics, "
              "not predictive quality. Preserved real-data temporal quality evidence belongs to the original engine reports.", ""]
    (args.output / "comparison.md").write_text("\n".join(lines))
    print(json.dumps({k: summary[k] for k in ("complete", "verified_cases", "expected_cases", "independent_prediction_comparisons")}))


if __name__ == "__main__":
    main()

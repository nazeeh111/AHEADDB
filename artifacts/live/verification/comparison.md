# AHEADDB Live frozen workload results

Status: complete. 90/90 cases verified; 0 invalid/unfinished files.

Frozen protocol SHA-256: `a0be8c5c6e531e97585a51357330d04161a1044f523fed1043beee566fff7de0`.

Each row is the median of the reported repetitions. Latency percentiles are computed per run, then medianed; they are not pooled. The 1,000 closed-loop reads per run sample response states, not continuous uptime. Only every tenth read opts into older results. Unavailable responses combine rejected freshness and service failure; the benchmark does not classify their causes. A zero unknown sample is not proof that outage detection never fails.

| Workload | Policy | n | CPU s | Lag p95 s | Read p95 ms | Miss % | Fresh sample % | WAL MiB | Refresh batches |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sparse | current | 3 | 113.54 | 73.257 | 477.2 | 99.6 | 38.7 | 6.88 | 240 |
| sparse | full | 3 | 125.35 | 81.573 | 542.2 | 99.6 | 34.5 | 6.59 | 240 |
| sparse | fifo | 3 | 64.56 | 32.807 | 257.9 | 98.3 | 33.4 | 6.95 | 240 |
| sparse | debounce | 3 | 51.27 | 2.168 | 243.9 | 48.8 | 87.2 | 5.03 | 38 |
| sparse | budget | 3 | 64.71 | 2.149 | 509.1 | 55.4 | 87.1 | 5.11 | 62 |
| dense | current | 3 | 140.61 | 103.234 | 541.5 | 98.3 | 14.1 | 17.87 | 240 |
| dense | full | 3 | 144.73 | 94.236 | 513.1 | 98.8 | 14.9 | 18.32 | 240 |
| dense | fifo | 3 | 183.58 | 132.594 | 671.6 | 98.3 | 13.3 | 17.99 | 240 |
| dense | debounce | 3 | 131.23 | 6.089 | 1208.3 | 86.7 | 86.9 | 16.89 | 62 |
| dense | budget | 3 | 69.04 | 2.770 | 485.4 | 55.8 | 87.2 | 16.73 | 62 |
| skew | current | 3 | 70.64 | 39.002 | 246.8 | 98.3 | 35.4 | 6.66 | 240 |
| skew | full | 3 | 101.78 | 55.744 | 361.4 | 97.9 | 37.5 | 6.34 | 240 |
| skew | fifo | 3 | 103.62 | 61.823 | 435.8 | 99.2 | 41.3 | 6.72 | 240 |
| skew | debounce | 3 | 18.84 | 1.238 | 72.7 | 37.9 | 87.0 | 4.78 | 18 |
| skew | budget | 3 | 39.61 | 1.729 | 272.7 | 39.2 | 87.6 | 4.92 | 62 |
| bursts | current | 3 | 50.24 | 24.734 | 170.9 | 97.5 | 34.1 | 6.16 | 240 |
| bursts | full | 3 | 128.91 | 88.314 | 492.5 | 99.2 | 31.2 | 6.40 | 240 |
| bursts | fifo | 3 | 40.50 | 21.482 | 135.7 | 97.9 | 33.3 | 6.37 | 240 |
| bursts | debounce | 3 | 54.25 | 1.912 | 242.2 | 51.5 | 87.2 | 4.91 | 45 |
| bursts | budget | 3 | 22.41 | 1.228 | 129.0 | 7.1 | 87.7 | 5.04 | 57 |
| silence | current | 3 | 32.82 | 6.697 | 82.9 | 95.8 | 93.7 | 1.01 | 24 |
| silence | full | 3 | 19.58 | 5.073 | 47.7 | 70.8 | 93.3 | 0.85 | 24 |
| silence | fifo | 3 | 13.32 | 1.881 | 14.6 | 41.7 | 93.3 | 0.85 | 24 |
| silence | debounce | 3 | 24.97 | 2.311 | 80.1 | 58.3 | 98.3 | 0.78 | 8 |
| silence | budget | 3 | 11.99 | 1.190 | 15.0 | 4.2 | 96.9 | 0.80 | 11 |
| overload | current | 3 | 84.12 | 45.496 | 257.6 | 98.3 | 41.9 | 6.44 | 240 |
| overload | full | 3 | 97.45 | 52.707 | 322.4 | 98.8 | 37.4 | 6.53 | 240 |
| overload | fifo | 3 | 63.95 | 37.201 | 211.9 | 97.9 | 35.1 | 6.99 | 240 |
| overload | debounce | 3 | 67.55 | 2.047 | 426.3 | 53.8 | 87.4 | 5.03 | 53 |
| overload | budget | 3 | 66.18 | 2.441 | 518.9 | 61.3 | 87.1 | 5.48 | 62 |

## Interpretation limits

All methods use identical owned-source persistence, HTTP transport, snapshots, leases and publication boundaries. `current` uses the original numerical Engine through the FIFO durability adapter and is algorithmically identical to `fifo`; it is a repeated control, not a separate competing algorithm. Full/FIFO already share compatible contexts. Coalescing can skip intermediate publications only when a newer committed snapshot covers the edits.

Four views share two history contexts. Normal views have 0.4-second freshness budgets; short-history views have 2-second budgets. Lag uses actual PostgreSQL source/publication commit timestamps; misses use persisted job deadlines. The JSON includes per-view opportunities/misses, setup, end-to-end throughput, source rows, sampled stale/unknown/unavailable fractions, post-sample drain, prediction work and memory/storage counters.

CPU combines the Python service/worker/client process and sampled PostgreSQL process-tree accounting, including ingestion and persistence. WAL/buffer counters are database-wide work counters, not physical device throughput. PostgreSQL peak footprint is sampled every 50 ms and shared-memory accounting is platform dependent. Python peak RSS includes setup. Setup is reported separately; agreement verification occurs after measurement. This is one shared Mac under variable unrelated load, not an isolated capacity or production SLA test. Three repetitions do not establish statistical significance or a universal speed advantage.

Every completed case reaches the acknowledged final revision for all views and independently checks every retained public generation against the same snapshot, cutoff and model. This demonstrates finite-workload progress, not a starvation theorem for unbounded arrivals. Synthetic stress streams validate serving mechanics, not predictive quality. Preserved real-data temporal quality evidence belongs to the original engine reports.

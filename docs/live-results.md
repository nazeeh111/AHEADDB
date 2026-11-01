# AHEADDB Live: frozen workload findings

All **90 cases** completed: six workloads, five policies/adapters and three
repetitions. Independent row-wise recomputation checked **25,576 view-generations**
in **12,792 publication batches**, making **1,636,864 prediction comparisons**.
Every case reached its acknowledged final source revision for all four views.
All 15 source-silence cases verified four fresh views and zero new refresh batches
during the three-second quiet check. This is finite-workload progress, not a
starvation guarantee under unbounded arrivals.

The result is mixed. Coalescing cut intermediate publication work and observed
lag versus FIFO. The budget planner had lower median deadline-miss fractions in
all six workloads, but lower measured CPU in only four. Debouncing remained
competitive and sometimes better. Tight deadlines were frequently missed; this
experiment does not establish a production freshness guarantee or general speed
advantage.

## Budget planner versus durable FIFO

Values below come directly from the frozen summary. Each is the median of three
runs; p95 is computed within each run before taking the median. Misses include
all four views. Normal-history views have 0.4-second budgets and short-history
views have two-second budgets. CPU includes Python service/worker/client and
PostgreSQL work, including ingestion and persistence.

| Workload | FIFO CPU s | Budget CPU s | FIFO lag p95 s | Budget lag p95 s | FIFO miss % | Budget miss % | Budget miss range % |
|---|---:|---:|---:|---:|---:|---:|---:|
| sparse | 64.56 | 64.71 | 32.807 | 2.149 | 98.3 | 55.4 | 55.4–64.2 |
| dense | 183.58 | 69.04 | 132.594 | 2.770 | 98.3 | 55.8 | 45.4–75.0 |
| skew | 103.62 | 39.61 | 61.823 | 1.729 | 99.2 | 39.2 | 4.2–47.1 |
| bursts | 40.50 | 22.41 | 21.482 | 1.228 | 97.9 | 7.1 | 4.2–10.8 |
| silence | 13.32 | 11.99 | 1.881 | 1.190 | 41.7 | 4.2 | 0.0–70.8 |
| overload | 63.95 | 66.18 | 37.201 | 2.441 | 97.9 | 61.3 | 56.7–62.5 |

Sparse CPU was effectively unchanged; overload CPU was higher with the budget
planner. Sparse and overload read p95 also worsened versus FIFO, from 257.9 to
509.1 ms and 211.9 to 518.9 ms respectively. Against debouncing, budget CPU and
deadline misses both improved in dense, burst and silence medians; sparse and
skew used more CPU, and sparse, skew and overload had more deadline misses.
The [complete five-method table](../artifacts/live/verification/comparison.md)
retains every comparator, including full rebuild and the original-engine adapter.

`current` and `fifo` are the same numerical algorithm under the same durability
adapter. Their median CPU differed materially, with current/FIFO ratios ranging
from 0.68 to 2.46 across workloads. This repeated-control variation is a direct
reason to treat policy rankings as observations from this shared host, not clean
causal estimates. Three repetitions do not establish statistical significance.
The frozen workload, seeds, code and reporting rules were not tuned after the run.

## Per-view deadline failures

These are total missed opportunities across all three repetitions, rather than
medians. Each task has 360 opportunities, or 36 for silence. Return and revenue
have identical counts in each budget-planner history context. The aggregate
counts can differ substantially from the median-run percentages above.

| Workload | Return, 0.4 s | Revenue, 0.4 s | Return short, 2 s | Revenue short, 2 s |
|---|---:|---:|---:|---:|
| sparse | 353/360 (98.1%) | 353/360 (98.1%) | 67/360 (18.6%) | 67/360 (18.6%) |
| dense | 330/360 (91.7%) | 330/360 (91.7%) | 93/360 (25.8%) | 93/360 (25.8%) |
| skew | 213/360 (59.2%) | 213/360 (59.2%) | 4/360 (1.1%) | 4/360 (1.1%) |
| bursts | 53/360 (14.7%) | 53/360 (14.7%) | 0/360 (0.0%) | 0/360 (0.0%) |
| silence | 13/36 (36.1%) | 13/36 (36.1%) | 5/36 (13.9%) | 5/36 (13.9%) |
| overload | 357/360 (99.2%) | 357/360 (99.2%) | 76/360 (21.1%) | 76/360 (21.1%) |

In particular, the normal-history sparse and overload views missed nearly every
0.4-second deadline. The planner preserves source boundaries and explicit read
rejection; it does not make these deadlines achievable on this host.

## Request samples and resource costs

The following supplementary table includes every method. Throughput is committed
source edits divided by the measured end-to-end interval, not maximum ingest
capacity. Dense edits change 64 source rows each; other edits change one. The
JSON also retains source-row throughput, setup time, prediction rows, buffer
counters and post-sample drain for each run.

| Workload | Method | Commits/s | Read p95 ms | Stale sample % | Unavailable sample % | Python peak MiB | PG sampled peak MiB | WAL MiB | Retained MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sparse | current | 0.93 | 477.2 | 6.1 | 55.2 | 213.0 | 22.4 | 6.88 | 6.12 |
| sparse | full | 0.75 | 542.2 | 6.5 | 59.0 | 213.2 | 23.3 | 6.59 | 6.10 |
| sparse | fifo | 1.96 | 257.9 | 6.6 | 60.0 | 215.3 | 22.9 | 6.95 | 6.12 |
| sparse | debounce | 1.66 | 243.9 | 1.2 | 11.6 | 213.8 | 22.1 | 5.03 | 5.05 |
| sparse | budget | 1.33 | 509.1 | 1.2 | 11.7 | 214.0 | 26.7 | 5.11 | 5.16 |
| dense | current | 0.83 | 541.5 | 8.5 | 77.4 | 235.1 | 22.6 | 17.87 | 16.03 |
| dense | full | 0.87 | 513.1 | 8.5 | 76.6 | 239.0 | 34.2 | 18.32 | 16.04 |
| dense | fifo | 0.62 | 671.6 | 8.6 | 78.1 | 238.0 | 30.1 | 17.99 | 16.02 |
| dense | debounce | 0.71 | 1208.3 | 1.2 | 11.9 | 237.8 | 32.3 | 16.89 | 15.11 |
| dense | budget | 1.74 | 485.4 | 1.2 | 11.6 | 242.6 | 28.9 | 16.73 | 15.13 |
| skew | current | 1.76 | 246.8 | 6.4 | 58.2 | 214.6 | 23.6 | 6.66 | 6.13 |
| skew | full | 1.10 | 361.4 | 6.2 | 56.3 | 213.8 | 22.1 | 6.34 | 6.10 |
| skew | fifo | 1.08 | 435.8 | 5.8 | 52.9 | 213.1 | 24.3 | 6.72 | 6.14 |
| skew | debounce | 6.13 | 72.7 | 1.3 | 11.7 | 214.7 | 21.2 | 4.78 | 4.91 |
| skew | budget | 2.85 | 272.7 | 1.2 | 11.2 | 214.1 | 20.0 | 4.92 | 5.12 |
| bursts | current | 2.55 | 170.9 | 6.5 | 59.4 | 215.5 | 20.1 | 6.16 | 6.09 |
| bursts | full | 0.84 | 492.5 | 6.9 | 61.9 | 213.4 | 23.9 | 6.40 | 6.10 |
| bursts | fifo | 3.13 | 135.7 | 6.6 | 60.1 | 215.2 | 20.4 | 6.37 | 6.09 |
| bursts | debounce | 2.07 | 242.2 | 1.3 | 11.5 | 214.9 | 19.9 | 4.91 | 5.05 |
| bursts | budget | 5.29 | 129.0 | 1.2 | 11.1 | 215.2 | 20.0 | 5.04 | 5.16 |
| silence | current | 0.27 | 82.9 | 0.6 | 5.7 | 213.3 | 21.6 | 1.01 | 1.40 |
| silence | full | 0.53 | 47.7 | 0.6 | 6.1 | 214.1 | 19.5 | 0.85 | 1.39 |
| silence | fifo | 0.86 | 14.6 | 0.6 | 6.0 | 214.0 | 19.5 | 0.85 | 1.38 |
| silence | debounce | 0.31 | 80.1 | 0.1 | 1.6 | 213.8 | 19.3 | 0.78 | 1.32 |
| silence | budget | 0.92 | 15.0 | 0.3 | 2.8 | 214.3 | 19.9 | 0.80 | 1.32 |
| overload | current | 1.50 | 257.6 | 5.8 | 52.3 | 214.6 | 19.9 | 6.44 | 6.09 |
| overload | full | 1.27 | 322.4 | 6.2 | 56.4 | 214.0 | 25.3 | 6.53 | 6.09 |
| overload | fifo | 1.98 | 211.9 | 6.5 | 58.4 | 215.0 | 23.0 | 6.99 | 6.09 |
| overload | debounce | 1.25 | 426.3 | 1.2 | 11.4 | 214.4 | 21.5 | 5.03 | 5.10 |
| overload | budget | 1.41 | 518.9 | 1.3 | 11.6 | 213.3 | 25.1 | 5.48 | 5.16 |

Each case has 1,000 closed-loop reads. Only every tenth read permits older
results. The fresh/stale/unavailable fractions describe those requests, including
settled periods; they are not continuous uptime. Unknown coverage was zero in
these read samples, which does not prove outage detection infallible. Unavailable
combines rejected freshness and service failure without classifying their causes.
Independent fault tests, including real worker and PostgreSQL loss, are reported
in the [verification matrix](live-verification.md).

Python peak RSS includes setup. PostgreSQL footprint is sampled every 50 ms and
shared-memory accounting is platform dependent; do not sum it with Python RSS
as a precise total-memory measure. WAL and buffer counters measure database work,
not physical device throughput. Coalescing publishes a newer covering snapshot
instead of every intermediate revision. It does not silently discard source edits
or weaken the freshness contract. Separate latest-view reads remain non-atomic.

## Evidence and reproduction

The corrected confirmation cohort is separate from the retired pre-correction
cohort. The bundle contains the exact 90 result texts and their hashes:

- [Frozen summary and per-run observations](../artifacts/live/verification/comparison.json)
- [Exact compressed case texts](../artifacts/live/verification/cases.json.gz)
- [Frozen workload protocol](../artifacts/live/verification/protocol.json)
- [Freeze identities](../artifacts/live/verification/experiment-freeze.json)

From a checkout or extracted source archive, reproduce the summary without a
database or package installation:

```sh
python3 -I scripts/summarize_live.py \
  --input artifacts/live/verification --output .runtime/reproduced-live
```

The bundled-input command reproduced byte-identical JSON and Markdown from a
clean source-archive extraction. The reader is frozen
at SHA-256 `4ae5ad3e7294a827e36896edf13e57815f60c2cceb93d6f15c82d19f5f2a522a`.
The protocol SHA-256 is
`a0be8c5c6e531e97585a51357330d04161a1044f523fed1043beee566fff7de0`.
All 17 measured code files still match the protocol.

These synthetic workloads validate serving mechanics. They do not validate
forecast accuracy. The preserved [real-data temporal-quality results](v020-results.md)
remain a separate record, including their negative outcomes and small-sample limits.

# AHEADDB 0.2.0: quality and refresh results

This release adds richer past-only features, purged temporal model selection and indexed refresh execution. It preserves the v0.1.0 release and every first-stage result. The evidence supports improvements in specific tasks and workloads, with material regressions and limits below.

## Prediction quality

The first fixed experiment improved calibration but exposed weak model selection: averaging rankings across just two small individual dates could miss temporal variation. Its source and results are preserved at commit `c2c392d` and in [`v020-quality-summary.md`](../artifacts/results/v020-quality-summary.md). Those periods became development data for the revised method. They are not independent confirmation of the final selector.

The final method holds out the last 20% of training cutoffs, bounded to 2–12 dates, as one chronological validation block. Every candidate fits only a prefix whose labels mature before the block begins. The task metric is computed on pooled validation rows, then the winner is refitted on all mature outer training samples. No final test label enters this choice. Selection records are exposed through `Engine.inspect()`, the API and the inspector's **Inspect task** dialog.

Candidates include the existing prior/mean and histogram models, a median regression baseline, regularized binary logistic regression, stronger-regularized boosting with absolute-error loss for MAE tasks, and a conventional persistence estimator. Persistence projects future count as history count/history days*horizon; sums use the latest observed value times that rate. Binary persistence probabilities fit a one-variable logistic calibration on training labels. No domain-specific station/customer lookup or LLM is involved.

New features retain the previous count/sum/mean/missing/recency aggregates and add standard deviation, latest nonmissing value, latest-minus-mean trend and nested 1/7/30-day counts/sums/means inside the declared history. Every record still passes both event and availability cutoffs. Tied event/availability times use the stable row key; input row order does not change features. Missing values remain visible through missing counts.

### Fixed February confirmation

After the disclosed first-stage development, February dates were fixed before running the revised method. Training ended before 2017-01-01. Eight of nine prescribed dates retained complete labels, yielding 96 station-day observations per task. Both tasks selected persistence using earlier training data only.

| Metric | Actual v0.1.0 histogram | v0.2.0 adaptive | Interpretation |
|---|---:|---:|---|
| Binary ROC AUC |0.60392|0.87843|Higher ranking quality in this period |
| Binary average precision |0.60956|0.91615| Higher precision/recall quality |
| Binary Brier |0.31192|0.16414|Lower probability error |
| Regression MAE |53.70261|47.75588|11.07% lower |
| Regression RMSE |75.14144|64.26858|14.47% lower |

![Fixed confirmation results](../assets/v020-quality.svg)

This is a short public-data confirmation, not external validation. Observations share stations and dates; 96 labels are not 96 independent trials. Complete-case selection and assumed source availability clocks/timezones remain limitations. The historical control has better ECE in this period (0.09444 versus adaptive 0.16489), despite worse AUC/Brier. Binary calibration is improved on some measures, not solved.

### Regressions retained

The final selector improves binary AUC in all evaluated development periods, but numerical gains are inconsistent. Later 2016–January 2017 Beijing MAE worsens 48.419→54.406 and RMSE 64.117→97.005. Retail gross-spend MAE improves in the original summer/autumn periods, but worsens 250.193→260.249 in the overlapping later November check; RMSE worsens in every retail period. Optimizing MAE can sacrifice large-value tail accuracy. All 12 negative legacy spend estimates disappear in these evaluated rows, but outputs are still generally unconstrained and future nonnegative estimates are not guaranteed.

All retail periods are retrospective because the complete later horizon overlaps already exposed outcomes. Neither first-stage nor development gains are presented as untouched evidence. [`v020-confirmation-summary.md`](../artifacts/results/v020-confirmation-summary.md) includes every task, period, candidate choice, quality/calibration tradeoff and control. The raw comparisons assert identical entities/labels and reproduce the original released pipeline, not an approximation using new features. The first stage reproduced 28,818 original histogram predictions exactly; the second stage reproduced 112,026 control predictions exactly.

## Incremental refresh

The old profile spent most of its sparse-refresh time repeatedly walking model state through joblib's Python serializer. Model fingerprints now use C-backed in-memory serialization plus SHA256, still detect mutable estimator changes, and are computed once per view per refresh. No serialized model is loaded or persisted. Snapshot ingestion also constructs row-key→owner and owner→event-position indexes. Sparse feature computation gathers an owner's rows before temporal filtering; missing counts use vectorized aggregates instead of per-group Python callbacks.

The paired benchmark loads actual v0.1.0 code from immutable commit `7c84f7a` and runs each version's real feature compiler/histogram model. Both versions receive the same tasks and source stream. New features are richer, so this is a whole-release pipeline comparison rather than an isolated algorithm ablation. Each version must equal its own independently computed full reference. Seven repeats interleave modes in a fixed randomized order for each scenario, with process CPU time and wall time saved.

At 2,000 entities, median wall time:

| Update | v0.1 incremental ms | v0.2 incremental ms | v0.2 full ms |
|---|---:|---:|---:|
| No change |35.30|6.68|44.88|
| Insertion |56.31|34.62|47.09|
| Late arrival |52.72|36.67|40.09|
| Value update |63.86|22.99|41.26|
| Foreign-key move |49.87|26.32|54.65|
| Deletion |50.52|30.54|45.93|
| High-degree insertion |55.72|33.27|38.13|
| Entity deletion |49.51|17.27|47.60|
| Clock advancement |102.43|45.26|42.34|
| Dense update |149.24|86.82|47.43|
| Model revision |47.86|24.85|48.20|

![Paired refresh measurements](../assets/v020-refresh.svg)

All 770 comparisons (two sizes, five version/mode combinations, 11 scenarios, seven repeats) matched full reference predictions exactly. Every v0.2 incremental scenario had a lower observed median than v0.1 incremental at both 400 and 2,000 entities. CPU medians also improve. The common sparse insert/update/move/delete scenarios, including late arrivals, at 2,000 entities improve wall median by 30.5–64.0%. These are local synthetic measurements, not production latency promises.

Dense updates remain 1.83 times slower than v0.2 full recomputation at 2,000 entities because invalidation discovery adds cost while nearly everything must be rebuilt. Clock advancement also loses slightly to full. Callers can explicitly select `mode="full"` for known broad refreshes; there is no claim that incremental always wins.

### Ingestion and memory cost

Snapshot validation/fingerprints/index construction is excluded from refresh timing and measured separately. On the 2,000-entity insertion snapshot the new owner/position indexes retain approximately 4.55 MB of additional pandas/NumPy storage, alongside 5.23 MB of source-table storage and 6.78 MB of fingerprint storage. These deep-storage estimates can count shared referenced strings and are not unique heap allocation. The tradeoff is substantial. Cached views/contexts and all snapshots also occupy RAM; process RSS is cumulative across both versions and imports, not per-mode allocation.

Single ingestion timings are noisy: the 400-entity insertion snapshot costs 0.340 s new versus 0.242 s old, while the 2,000-entity sample happens to be faster new. Do not infer a universal ingestion speedup. No trusted change feed, production write, database trigger, persistent cache or arbitrary multihop dependency tracking was added. Source diff still compares keyed row indexes; building the immutable snapshot still scans the source.

## Reproduction and checks

```sh
uv sync --frozen --extra dev --extra mcp
uv run --no-sync pytest -q
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 LOKY_MAX_CPU_COUNT=1 \
  uv run --no-sync python scripts/replay_v020.py --dataset all --dest run/quality-replay
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 LOKY_MAX_CPU_COUNT=1 \
  uv run --no-sync python scripts/benchmark_v020.py
uv run --no-sync python scripts/verify_v020.py
```

Use a full Git clone for paired comparisons: the evaluator loads the immutable legacy commit from local Git history. Download the two attributed UCI datasets with `scripts/fetch_public_data.py` first. The portable replay wrapper uses the unchanged confirmation evaluator and a fresh output folder; it was added after the numerical experiment only for portable temporary storage and saved-output comparison. It checks its immutable protocol and preserved first-stage artifact hashes before running. To reproduce the original first-stage selector, use commit `c2c392d`; running its evaluator against the final selector would be a different experiment. Do not overwrite the preserved first-stage records. Use separate output folders for exploratory changes.

Local suite: 76 passed, 7 optional/service skips; the 26 new independent review cases cover a row-loop feature oracle, sparse/full/order equivalence, model mutation, selection training exclusion and heldout poisoning. Two reproduced defects were fixed: inspection metadata aliasing and the single-cutoff selector fallback. Optional FastDFS and actual PostgreSQL checks are run separately. The final release waits for CI, clean installation/API checks, documentation links and uploaded asset checksum verification. See [review](v020-review.md), [first-stage protocol](v020-evaluation-protocol.md), [confirmation protocol](v020-confirmation-protocol.md), [data licenses](data.md) and release notes for final remote verification.

## Migration

The default backend is now `adaptive`; `backend="histogram"` remains available. Existing explicit project choices continue to work, with new backend names accepted. The aggregate/preprocessing version changes, so retrain models rather than reuse 0.1 feature columns. Serving remains process-local and read-only. The inspector's numerical values and recorded playback were regenerated from the current 0.2 source; v0.1media/results remain recoverable from its public tag/release. Neither numerical quality nor refresh performance is guaranteed for unsupported workloads.

# Public data experiments

The release reports executed predictions on two public UCI datasets. Downloaded source archives and generated DuckDB snapshots stay in ignored `.data/`. Saved aggregate metrics and prediction records are in `artifacts/results/public-*.json` and `.csv`. These experiments are separate from the synthetic update-stream stress tests.

## Sources and attribution

| Dataset | Source and attribution | License |
| --- | --- | --- |
| Online Retail | Chen, D. (2015). *Online Retail*. UCI Machine Learning Repository. [Dataset and citation](https://archive.ics.uci.edu/dataset/352/online+retail), [DOI](https://doi.org/10.24432/C5BW33). | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Beijing Multi-Site Air Quality | Chen, S. (2017). *Beijing Multi-Site Air Quality*. UCI Machine Learning Repository. [Dataset and citation](https://archive.ics.uci.edu/dataset/501/beijing+multi+site+air+quality+data), [DOI](https://doi.org/10.24432/C5RK5G). | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

The linked UCI pages stated CC BY 4.0 when inspected on 2026-10-26. Source archive SHA256 digests, byte counts, transformations, row counts and snapshot versions are saved with the results. The source dataset license applies to derived prediction records; the software license does not replace it.

## Temporal assumptions

Neither dataset records ingestion or availability timestamps. For a reproducible experiment, retail invoices become available 24 hours after the recorded invoice time; Beijing daily measurements become available at the following midnight. Naive source clocks are interpreted as UTC. These are explicit assumptions, not verified historical ingestion behavior. The experiments cannot establish performance under the real source's late arrivals or publication delays.

All historical feature joins require both event time and assumed availability time to be at or before the observation cutoff. Future labels use the stated horizon and label-maturity delay. Every training label matures by the model's `fit_as_of`, which precedes every held-out cutoff. Models use fixed parameters, with no test-set hyperparameter search or test-dependent calibration. Source availability assumptions remain a material limit even when these chronology checks pass.

## Tasks and sampling

**Retail.** Normalize positive invoice lines into customers and invoices. Drop missing customer IDs, cancelled invoices, nonpositive quantities and nonpositive prices. Sum positive line spend per invoice/customer/time. The two tasks predict any purchase in the next 30 days and total positive gross spend in that horizon, with 90 days of available history and a one-day label delay. This is not a net-revenue forecast. Customers first appear at their first assumed available invoice; future customer membership is not available to earlier predictions. Summer and autumn held-out cutoffs expose seasonal change.

**Beijing.** Normalize hourly observations into 12 stations and daily measurements. A daily PM2.5 value requires at least 18 observed hourly values. Forecast next-day mean PM2.5 and whether it exceeds 75 micrograms/m³; 75 is an experiment threshold, not a health recommendation. Use seven days of history. Only cutoffs with an observed next-day target for every station enter training or evaluation. Skipped cutoff counts are recorded. Missing target measurements are unknown, never zero pollution. This complete-case selection may bias results toward better observed days. The database is normalized station time-series data, not a multi-hop relational benchmark. The 2015 and 2016 held-out periods test temporal change.

## Comparisons and measurements

- **Historical:** fixed task-specific rate/persistence estimate. Retail binary uses a Poisson estimate from historical invoice rate; gross spend scales historical spend by horizon/history. Beijing regression uses the historical seven-day mean and binary uses the fraction of observed historical days above 75.
- **Training baseline:** prior class probability or mean target, fitted only to mature training examples.
- **Histogram model:** the engine's fixed-budget histogram gradient boosting on relational aggregates. Same feature compiler, training cutoffs, test entities and held-out targets as the other comparisons.
- **Optional FastDFS:** the separately installed released relational feature pipeline uses the exact same fold definitions. Its dependency and model limits are described in the research and optional-backend documentation. It is not a pretrained foundation model.

Binary metrics include ROC AUC, average precision, Brier score and five-bin reliability/ECE. Numerical metrics include MAE and RMSE. Raw binary probabilities are not guaranteed to be calibrated. Runtime includes actual feature construction, fitting and prediction as recorded. The final source evaluation limited model fitting/prediction to one thread while other work used the host; snapshot reads separately permit two DuckDB threads. Elapsed times are observed host measurements, not a controlled throughput ranking between algorithms. Peak RSS is the cumulative process memory high-water mark, including loaded data; it is not an isolated per-model allocation measurement. Entity IDs recur across times and samples are dependent, so no IID confidence interval is claimed.

## Observed results and failures

The executed comparison produced the following pooled held-out results. Numbers are rounded here; JSON and CSV retain the underlying values and individual folds. Pooled ROC AUC for a prior model can differ from 0.5 because its fitted probability changes between time folds.

| Task | Held-out samples | Historical | Training prior / mean | Histogram boosting | FastDFS + ExtraTrees |
| --- | ---: | ---: | ---: | ---: | ---: |
| Retail purchase, ROC AUC ↑ | 14,025 | 0.699 | 0.476 | 0.698 | 0.696 |
| Retail gross spend, MAE GBP ↓ | 14,025 | 227.32 | 318.31 | 237.23 | 234.13 |
| Beijing PM2.5 above 75, ROC AUC ↑ | 384 | 0.502 | 0.491 | 0.355 | 0.307 |
| Beijing PM2.5, MAE micrograms/m³ ↓ | 384 | 39.92 | 38.41 | 51.81 | 51.41 |

The more complex model did not consistently win. Retail purchase boosting improved Brier score from the historical baseline's 0.1870 to 0.1759, while its ROC AUC was slightly lower. Retail spend boosting lost to the historical baseline. It also emitted eight negative gross-spend estimates; the generic numerical model is unconstrained and no test-dependent clipping was applied. In the actual-spend top decile (at least GBP 540.986, 1,403 samples), boosting MAE was GBP 1,263.00 versus the historical baseline's GBP 1,168.80. Its largest single absolute error was GBP 66,159.09. These tails matter even when overall MAE seems tolerable.

Beijing boosting failed: its 2015 binary ROC AUC was 0.252, its 2016 value was 0.517, and its numerical MAE exceeded both simple baselines. The released FastDFS pipeline also failed on these pollution tasks: pooled ROC AUC was 0.307 and MAE was 51.41. Those results do not support using these bounded pollution models for an application decision. The short historical feature sets and temporal change are possible explanations, not established causes.

Calibration also changes over time. Retail boosting's five-bin ECE was 0.0032 in summer and 0.0592 in autumn; observed purchase prevalence moved from 0.2655 to 0.3057. Good calibration in one period does not establish calibration in a later one. No held-out outcomes were used to recalibrate or select parameters after seeing these failures.

The executed optional released FastDFS comparison is saved in `artifacts/results/fastdfs-*.json` and included in both public-result charts. It uses the same source snapshots and folds, with a different relational feature set and 80 ExtraTrees trees. Both estimators limit model fitting/prediction to one thread; FastDFS feature queries use upstream DuckDB thread defaults, which were not explicitly constrained. Matching iteration/tree counts bounds model size but does not equalize computational work or the feature pipelines. These experiments measure predictive quality separately from AHEADDB's maintained-view correctness and refresh performance.

## Reproduce

```sh
python scripts/fetch_public_data.py --dataset all
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python scripts/evaluate_public.py --dataset all
```

Run from the repository root in the pinned AHEADDB environment. Fetching uses only official UCI static archives and needs no account or API key. Parsing the retail workbook can take longer than model fitting. A rerun reuses a source-hash and snapshot-version verified local copy. Modified or unrecognized snapshots are preserved and rejected; choose a fresh `--dest` instead of overwriting them. Evaluations are intentionally outside routine CI. See saved JSON for exact cutoffs, versions, assumptions, fold failures and metrics, and avoid interpreting these small bounded experiments as broad relational-model superiority.

To recreate the standard Matplotlib figures from saved numerical results without downloading data or fitting models:

```sh
uv venv --python 3.12 .runtime/plots
uv pip install --python .runtime/plots/bin/python -r scripts/requirements-plotting.txt
.runtime/plots/bin/python scripts/plot_public.py --dest .runtime/public-plots
```

The source release includes the successfully rendered quality and calibration SVG/PNG charts in `assets/`. Inspect a new render before replacing those files. An earlier trial with two native compute threads produced identical predictions but much slower runtimes on the observed host; retained release results use a single native compute thread.

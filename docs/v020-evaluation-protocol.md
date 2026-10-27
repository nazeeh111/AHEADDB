# v0.2.0 prediction evaluation protocol

Frozen before executing the adaptive backend on the final evaluation periods on 2026-10-27 UTC.
The source of truth for exact cutoffs is `frozen_protocol()` in `scripts/evaluate_v020.py`;
its canonical JSON SHA256 and this document's SHA256 are recorded in every result.
This protocol is a descriptive benchmark, not a formal statistical preregistration.

## Comparisons

Run the exact v0.1.0 source at tag `v0.1.0` (commit
`7c84f7aaa0c573340837667ae63a6c9c7b71331a`) under a separate Python import
namespace. Its original compiler must generate its original features, and its
original `fit(..., backend="histogram")` must train its original estimator.
Compare that executed baseline with the current compiler and
`fit(..., backend="adaptive")`, using identical tasks, snapshots, chronological
training cutoffs, fit-as-of times, test entity IDs and labels. Assert test entity
and label equality before recording scores. Also report v0.1.0 training-prior or
training-mean and task-specific historical persistence/rate controls.

The adaptive backend may select from its fixed implementation candidates using
only purged earlier chronological validation within the training cutoffs. Its
selection metadata must be saved. No evaluation-period scores may guide candidate
choices, features, hyperparameters, cutoff changes or selective reporting. If a
bug invalidates a run, retain the failure, explain the correction and rerun all
affected comparisons; do not silently substitute a successful variant.

## Fixed periods

1. **Previously exposed retrospective comparison.** Reuse all published summer
   and autumn retail folds and the 2015/2016 Beijing folds from
   `scripts/evaluate_public.py`. These were examined in v0.1.0 and informed the
   motivation for development. Improvements here are retrospective evidence.
2. **Later Beijing holdout.** Train on weekly cutoffs starting 2013-05-01 and
   ending no later than 2016-07-29; fit as of 2016-08-01. Evaluate weekly cutoffs
   from 2016-09-01 through 2017-01-26, inclusive. This period was outside the
   published evaluation folds. Use both the existing next-day PM2.5 regression
   and the PM2.5-above-75 binary task. The period is fixed before new-model
   evaluation, but public-data development is not independent external validation.
3. **Later overlapping retail check.** Use published initial training cutoffs
   plus 2011-06-15, 2011-07-01, 2011-07-15, 2011-08-01 and 2011-08-15, fit as of
   2011-10-01, test at 2011-11-08. Its complete 30-day horizon ends before the
   source ends. This horizon overlaps previously published November outcomes;
   it is explicitly not a blind or independent holdout. A materially independent
   later retail month is unavailable in this source.

Discard Beijing cutoffs only when an existing source label is missing for any
station, identically for both implementations. Record the proposed and retained
cutoffs and missing-label counts. Preserve complete-case selection bias in the
limitations. All training labels must have matured by fit-as-of; all test cutoffs
must follow fit-as-of. No random train/test split is allowed.

## Measurements and reproducibility

For each dataset, task, period and backend record sample count, actual source and
model versions, training/test cutoff lists and canonical test-row/label hash.
Report regression MAE and RMSE; report binary ROC-AUC, average precision, Brier
score, five-bin expected calibration error and reliability bins. Report negative
regression-estimate counts and minimum estimates, even when the true target is
nonnegative. Do not discard outliers or clip the legacy model after evaluation.

Record fitting and prediction seconds, current process peak RSS and runtime
versions. Peak RSS is a cumulative process high-water mark including source
loading and all preceding fits, not a backend's isolated allocation. Report it
without claiming per-model memory savings. Record source loading separately.
Use one numerical thread. Derived predictions may be saved for audit; downloaded
raw data and fitted estimators are excluded from release artifacts.

Evaluate every fixed task/period/backend, report failures and do not average
incomparable task units. Pool within a task only with the period category kept
explicit. Recurring entities, adjacent days and overlapping horizons create
dependence, so no IID confidence intervals or universal accuracy claim is made.
Availability clocks and timezone interpretation remain experimental assumptions.
Retail targets positive gross spend after excluding cancellations/refunds, not
net revenue. Beijing daily completeness and direct station history do not
constitute an original multihop relational benchmark. This benchmark does not
establish a foundation model or zero-shot transfer capability.

A new-model regression on any fixed period remains visible. Release summaries
must distinguish task-level gains, losses, calibration tradeoffs, runtime costs
and the limited genuinely later-period evidence. The benchmark does not promise
that a new backend beats every control.

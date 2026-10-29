# v0.2.0 second-stage development and confirmation protocol

Frozen before executing the revised selector on the February 2017 confirmation
period, on 2026-10-27 UTC. This is a sequential, disclosed development exercise;
it is not a retroactive claim that the first stage succeeded.

## Preserved first-stage evidence

Commit `c2c392d` preserves the original evaluator, source implementation, frozen
protocol and mixed first-stage results. Leave every `v020-quality*` artifact and
`scripts/evaluate_v020.py` unchanged. Every first-stage period, including its later
Beijing and overlapping later retail checks, is now exposed development evidence.
The second-stage selector was motivated by the first-stage results. Never call
its scores on those periods independent confirmation.

## Fixed second-stage implementation

The root implementation replaces two small per-date candidate score averages
with a single purged chronological validation block comprising the last 20% of
training cutoffs, bounded to 2 through 12 cutoffs. Candidates fit only on a prefix
whose labels mature before that block begins. Select using pooled validation rows
for the task's declared metric, then refit the chosen candidate on all outer
training rows. Preserve exact selection metadata and audit the purge.

The existing baseline, histogram and robust candidates retain their fixed
parameters, alongside the existing regression median and binary linear candidates. Add one generic deployable persistence candidate: future count
is historical count / history length * horizon; future sum is latest observed
value * historical count / history length * horizon. Regression uses the fixed
estimate. Binary prediction fits only a one-dimensional logistic calibration to
training labels. This design is fixed before confirmation scores. No feature,
candidate, parameter, selection or cutoff tuning is allowed after those scores.
Implementation bugs must be disclosed, with invalid evidence retained and every
affected comparison rerun; failures are not silently dropped.

## Frozen periods and comparisons

Reuse every first-stage task and fold for a development-only comparison with the
new final implementation. Also evaluate the existing Beijing binary
PM2.5-above-75 next-day task and PM2.5 next-day regression task on one new period:

- Fit as of 2017-01-01.
- Training cutoffs: weekly from 2013-05-01 through 2016-12-25, retaining dates whose
  next-day labels are complete across all stations and mature before fit-as-of.
- Confirmation cutoffs: 2017-02-01, 2017-02-04, 2017-02-07, 2017-02-10,
  2017-02-13, 2017-02-16, 2017-02-19, 2017-02-22 and 2017-02-25.
- Filter a cutoff only if a source target is missing for any station, identically
  across implementations. Record both proposed and retained dates and skip
  counts. The filter may inspect completeness, never use scores to choose dates.

These February dates were not evaluated in the first stage. This small public
source period is a newly fixed temporal confirmation, not independent external
validation. It has at most 108 station-day labels per task. No untouched complete
later retail horizon remains, so all retail results are retrospective.

Run the actual original v0.1.0 compiler and histogram model from immutable commit
`7c84f7aaa0c573340837667ae63a6c9c7b71331a`, the original training-prior/mean and
historical controls, and the current compiler/adaptive fit. Assert exactly the
same task/schema/source, test entity IDs and labels. Keep the original first-stage
adaptive metrics available for development-period comparison without rewriting
them. Never average February confirmation into development metrics.

## Measurements and interpretation

Retain the first-stage measurements: ROC-AUC, average precision, Brier, five-bin
ECE and reliability for binary tasks; MAE and RMSE for regression; negative counts
and estimate ranges; fit, test-feature compilation and prediction times; process
peak RSS, versions, source/implementation hashes and full selection metadata.
Record all fixed comparisons and every failure. Use one numerical thread, free
local compute, and no downloaded raw-data or fitted-model release artifacts.

Task metric wins and losses, calibration tradeoffs, tail-error changes and runtime
costs must remain visible. Compare the historical control as well as the old
histogram. Recurring entities, dependent days, complete-case label selection,
assumed source clocks/timezones and the single short confirmation period limit
generalization. Cumulative peak RSS is not per-model allocation; timings are
single runs, not stable machine benchmarks. No IID confidence interval, universal
quality claim, foundation-model claim or external-validation claim is supported.

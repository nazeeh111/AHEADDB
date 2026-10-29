# v0.2 independent correctness review

Scope: changed modeling, temporal feature compilation, snapshot event indexes,
refresh invalidation and project backend defaults relative to `7c84f7a`. The
review inspected the actual source and added controls in `tests/test_v020.py`.
The first reviewed implementation was recorded in `c2c392d`. The metadata copy
correction and second-phase pooled selector with persistence candidate were
reviewed afterward against the current source. The first-stage evaluation
prompted the method revision; it is not independent confirmation of that
revision. A separately preregistered confirmation protocol is needed for that.

## Findings

One introduced defect was reproduced: `Engine.inspect()` returned the live
model-selection dictionary. A caller could change the recorded chosen backend,
scores and nested fold cutoffs by editing an inspection response. This also
changed the model version. Returning a deep copy closes that alias. The review
test changes both top-level and nested fields and checks that live provenance
and the model version remain unchanged.

The second-phase selector initially raised `IndexError` for a single mature
training cutoff, because it indexed a nonexistent validation cutoff. The
previous adaptive implementation supported this input with a prior/mean
fallback. An explicit guard restores that behavior; the regression test fits
24 mature samples and checks the fallback result and absence of inner fits.

No additional correctness blocker was identified in the changed paths within
this review's scope. The combined core and review suite passed after the fix.

## Independent controls

- A Python row-loop oracle independently calculates every aggregate. It checks
  the left-open history interval, both event and availability clocks, exact
  cutoff boundaries, nested recent windows, missing and negative values,
  population standard deviation, recency and deterministic last-value ties.
  Property cases cover history lengths 1, 3, 7, 10, 30 and 40 days.
- Sparse indexed queries, the full-frame path and caller-requested entity order
  agree. Reordering source rows, including nonconsecutive dataframe labels,
  preserves feature values and the snapshot version. Returned event rows are
  defensive copies, including empty and unknown-owner requests.
- Candidate selection is checked against the actual fitted sample IDs with a
  recording estimator. Every candidate is fit once on the same fixed prefix,
  purged so all outcomes matured by the first validation cutoff. Later block
  dates cannot admit newer training samples. Cases with 3, 5, 20 and 70 cutoffs
  check the bounded block sizes and five candidate fits. An independent AUC
  pair-count example distinguishes pooled scoring from averaging per-date
  scores; a second case verifies pooling when each date has only one outcome
  class. The chosen candidate is then actually refit on all outer training
  samples. Deterministic candidate ties are checked. Binary and regression fits retain
  identical selection records and predictions after held-out future outcomes,
  late historical rows and late outcome rows are poisoned. The chosen candidate
  agrees with the declared metric's optimization direction.
- Histogram, robust histogram and linear model versions remain stable during
  prediction and change after actual fitted numerical state is mutated.
- Persistence count forecasts are checked against event rate times horizon;
  sum forecasts against that rate times the last nonmissing value, including
  zero and negative values and reordered inputs. Separate binary and regression
  fits ignore future and late rows. Binary calibration uses one training-only
  projection, returns finite bounded probabilities, and changes model version
  when its fitted coefficient is mutated.
- Indexed refresh is compared with full materialization after inserts,
  deletions, foreign-key moves, availability changes and event-time changes.
  Moves include a previously empty owner, and comparisons continue through
  clock advances that admit and expire history. Sparse feature-row counts are
  checked as well as all resulting features and predictions.

The last-value feature means the last **nonmissing** numeric value ordered by
event time, availability time and string primary key. Missing counts expose
missing source values. This is deterministic, but a missing latest event does
not erase the previous observed value.

## Verification and limits

Execution uses the project's `.venv`, with `OMP_NUM_THREADS=1`,
`OPENBLAS_NUM_THREADS=1` and `LOKY_MAX_CPU_COUNT=1`. The focused review command is
`python -m pytest -q tests/test_core.py tests/test_v020.py`; lint is
`ruff check tests/test_v020.py`.

First-stage results after the metadata copy correction: **58 tests passed in
27.30 seconds** (44 existing core cases and 14 review cases). Second-phase
current-source results: **70 tests passed in 52.97 seconds** (44 existing core
cases and 26 review cases); focused Ruff checks passed. Both reproduced
defects above are covered by passing regression controls.

Git blob identifiers captured after that run:

| Source | Blob |
| --- | --- |
| `src/aheaddb/modeling.py` | `bb45e53677077e29c797ea3de566c4af835969f0` |
| `src/aheaddb/compiler.py` | `c4cf550789d1cb199606f2d063297f3046d81f40` |
| `src/aheaddb/engine.py` | `955972779343b9a3053ecc278120151e7fff2d34` |
| `src/aheaddb/snapshot.py` | `5c19cc4d596e04807bed0d56eff9e57705465d55` |
| `src/aheaddb/project.py` | `e88bd003dba5097e1f1c92a8bd664ab6638db335` |
| `tests/test_v020.py` | `83f5d9e699cb2ac33fc591876797df373ff74d2e` |

This is a correctness review of bounded direct entity/event relations. It does
not establish predictive superiority, calibration, causal validity or a latency
speedup. Those claims require the separately recorded dataset protocol and
measurements, including ingestion time and index memory. The poisoning controls
test the implemented temporal contracts; they cannot establish that a supplied
source's timestamps truthfully represent when data became available.

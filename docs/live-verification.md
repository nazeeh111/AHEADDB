# AHEADDB Live verification and limits

Live adds an owned PostgreSQL source, durable refresh jobs and explicit source
coverage around the preserved statistical engine. It does not establish production
CDC correctness, a general exactly-once system or a predictive-quality improvement.
The [consistency contract](live-design.md) defines the boundary being tested.

## Executed corrected-source checks

The complete diagnostic suite passed **144 tests**, with six optional FastDFS
checks skipped, in 363.50 seconds. Ruff passed and mypy checked 22 source files.
The previous full run had 143 passes and one database-related 503 in a recovery
check. The isolated case then passed, followed by the complete diagnostic run.
Its original cause was not reproduced; the failure remains in task-local evidence.
A passing rerun is not proof that transient database outages cannot occur.

Independent source review first reproduced a scalar-identifier purge/restore bug
in the pre-release implementation. New edits now persist canonical string IDs.
Maintenance rejects ambiguous legacy raw identifiers before deleting/importing
rows, including retained history, rather than guessing ownership. Sixteen focused
checks cover the correction and generated concurrent writes. See the
[identifier and legacy-storage contract](live-design.md#source-ownership-and-coherent-boundaries).
The first incomplete confirmation cohort is retired; corrected confirmation uses
fresh source and the same workload/policy seeds. No old cases are combined with it.

A fresh non-editable corrected wheel executed the generic public SDK flow with
Python isolated mode, no client database setting and no source imports. Both tasks
advanced from revision 1 to 2 while retaining model/cutoff identities. The expected
value changed from 53.235 to 840.0. The example's six `folds` entries per task
are two eligible chronological test cutoffs evaluated with three baselines,
not six independent temporal holdouts. These synthetic integration values do not
measure forecast accuracy. [Installed developer receipts](../artifacts/live/verification/installed-developer.json).

The corrected installed shop committed an order at revision 2. A worker was killed
after computation and before publication. Default reads hid the uncovered result;
explicit older mode showed revision 1 against head 2. A replacement with a bounded
300-second lease published both views fresh at revision 2, showing 57.7% return
probability and expected revenue 315.0 for the selected synthetic customer.
The actual 18.79-second recording shortens idle gaps to at most three seconds.
Current desktop/mobile previews come from that corrected installed application;
390-pixel content fits the viewport and form fields have labels.
[Recovered publication receipt](../artifacts/live/verification/installed-recovered-batch.json).

Corrected-source native fault probes also passed: actual ENOSPC on a dedicated
64 MiB image, and an unclean PostgreSQL restart after verifying a separate backup
restore. The acknowledged revision survived WAL recovery and another edit worked.
[Storage probe](../artifacts/live/verification/actual-disk-full.json) ·
[Database recovery probe](../artifacts/live/verification/database-crash.json).

All 90 corrected frozen workload cases completed and verified 25,576 view-generations
in 12,792 publication batches, with 1,636,864 independent prediction comparisons.
Every case reached the final acknowledged revision for all views; all 15 silence
cases renewed coverage without a new refresh batch. The [measured findings](live-results.md)
report mixed CPU results, missed deadlines and substantial repeated-control variation.
A fresh public HTTPS checkout at `6179431` installed non-editably using an empty
dependency cache. Its isolated public SDK walkthrough advanced both features
from revision 1 to 2 without a client database setting or private project files.
The expected-value feature changed from 53.235 to 840.0, with model/cutoff fixed.
Both push and pull-request CI passed, including 144 tests and six optional skips.
[Public installation receipt](../artifacts/live/verification/public-install.json) ·
[CI steps](../artifacts/live/verification/public-ci.json).
Final publication and asset-download verification are recorded with the
[versioned release](https://github.com/nazeeh111/AHEADDB/releases/tag/v0.3.0).

## Independent numerical agreement

`tests/live_reference.py` constructs features row by row, separately from
`Compiler.features` and the incremental Engine. Tests retrieve actual public HTTP
answers, recover their identified snapshot, replay the same model, and compare
all eligible entity predictions at the same cutoff (`rtol=1e-12`, `atol=1e-10`).
This checks source membership and feature equivalence rather than comparing two
calls through the same incremental feature builder. It shares the public fitted
model's prediction implementation; it is not an independent implementation of
scikit-learn or a proof that a selected model is statistically appropriate.

The frozen workload experiment also checks every retained public view-generation
after each measured case. The [generated comparison](../artifacts/live/verification/comparison.md)
reports distinct publication batches and per-view generations separately.

## Failure and consistency matrix

| Scenario | Executed check | Required recovered behavior |
|---|---|---|
| Allocation order differs from commit order; concurrent writer/snapshot | Locked source transaction tests | Revision follows commit serialization; immutable snapshot has one identified boundary |
| Inserts, updates, deletes, late records, owner moves, advancing cutoff | Original engine tests plus real PostgreSQL edit/view tests | Public generation agrees with full row-wise features; both owners invalidate |
| Claim before/after commit, compute before/after, publication before transaction/before commit/after commit | Seven abrupt `os._exit` child-process boundaries, plus exception-path tests | Reclaim durable work, preserve complete generations, agree independently, then accept another edit |
| Source transaction before/after commit | Abrupt child-process exit at both boundaries | Retry exact request once; rollback before commit, deduplicate after commit; continue serving |
| Expired worker or cancellation | Delayed claimant publication | Fenced claimant cannot overwrite replacement work |
| Poison work, failed-job backlog, lower admission bound | Durable attempts/backpressure/retry tests | Bound admission atomically; existing work remains recoverable without admitting new jobs |
| Model declaration A→B→A at one source revision | Registration/retry/progress regression | New declaration epoch, truthful minimum progress, complete republished generation |
| Backward clock evidence followed by rejected read | Persisted clock guard and operator acknowledgement | Coverage stays unknown across connections; acknowledgement invalidates pointers and rebuilds |
| Failed migration | Transactional migration fault | No partial schema or changed published generation |
| Publication/source storage errors | PostgreSQL `53100` injection | Roll back entire generation/source transaction; preserve prior result and recover |
| Actual full volume | Dedicated 64 MiB HFS+ image with real ENOSPC | Failed source edit leaves revision 1; release filler, same request reaches revision 2 and both views recover |
| Actual PostgreSQL process loss | Immediate unclean stop of marked synthetic cluster | HTTP 503 while down; WAL retains acknowledged revision 2; agreement after restart and continued revision 3 |
| Retention, purge and portable restore | Source-bound ledger, replay/lock/hash, moved-owner and unlogged-purge tests | Active work/training protected; purged data unavailable; old backup requires current external ledger |
| Compressed large source; hostile HTTP data | Decoded-byte bounds, host/origin/body/error tests | Reject atomically without echoing private row contents |
| Source changes after separate example reads | Later-status regression | Default example hides the now-uncovered pair; explicit older mode labels it |

The actual database-stop probe first creates a `pg_dump` backup and verifies a
restore into a separate database. It then makes a newly acknowledged edit before
stopping PostgreSQL, so WAL recovery is tested beyond the backup's boundary.
The volume-fill and cluster-stop scripts refuse ordinary production targets.
Those manual native probes are not run automatically in CI.

## Reproduce proportionately

Use a dedicated disposable database and the pinned checkout:

```sh
uv sync --frozen --extra dev --extra live --extra mcp
export AHEAD_TEST_POSTGRES='postgresql://YOUR_LOCAL_ROLE@127.0.0.1:55439/postgres'
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 LOKY_MAX_CPU_COUNT=1 \
  uv run --no-sync pytest -q
uv run --no-sync ruff check src scripts tests examples/live_desk examples/live_developer.py
uv run --no-sync mypy src/aheaddb
uv build
```

PostgreSQL tests skip without the explicit test DSN. Optional FastDFS tests need
their separate extra. Do not call a skipped durability suite a passing database
check. PostgreSQL 15.16 on macOS arm64 is the local fault-test environment; CI
uses PostgreSQL 16 on Linux. Remote CI and release download checks require the
source to be published and remain separate gates.

The native benchmark needs a dedicated PostgreSQL cluster with
`track_commit_timestamp`, `track_io_timing` and `track_wal_io_timing` enabled.
Its Mac process accounting must be calibrated before use. See the frozen protocol
and raw cases for complete workloads, bounds and runtime observations. Run the
summary reader without a database:

```sh
python3 -I scripts/summarize_live.py \
  --input artifacts/live/verification --output .runtime/reproduced-live
```

## Predictive quality stays separate

The original [v0.2 temporal results](v020-results.md) retain chronological holdouts,
mature-label rules, availability assumptions, historical and tabular baselines,
calibration measures and negative development results. The numerical engine and
those records are preserved. Live's synthetic shop and activity streams test
integration, freshness and recovery; they cannot validate a business forecast.
A successful durable refresh can still serve a poorly chosen task or model.

## Operational limits

This is one local API and one worker over bounded owned data. Source silence can
renew coverage only after verifying that owned source; it cannot detect a missing
external feed. Separate latest-view reads are not mutually atomic. Leases do not
renew during an arbitrarily slow model fit. Recipe replay depends on matching
numerical/runtime identities. Backups are private data, and offline restoration
requires the owner's current purge ledger. Authentication, external CDC, automatic
rolling retraining, high availability and continuous WAL/PITR recovery are outside
this version. See [security guidance](../SECURITY.md) before using a real source.

## Local release archive check

Before publishing, build into a fresh directory and audit the actual archive bytes:

```sh
uv build --out-dir .runtime/release-final
uv run --no-sync python scripts/check_release.py --dist .runtime/release-final
```

The check compares packaged runtime files and examples with the tested checkout,
rejects task/Git/cache files, and requires all 90 frozen cases with matching raw
hashes and protocol/report-reader identities. Incomplete experiments deliberately
fail this release check. Public CI, clean-clone execution and downloaded asset
hashes remain separate verification steps. The source archive uses explicitly
selected project roots, including the preserved engine/quality artifacts.

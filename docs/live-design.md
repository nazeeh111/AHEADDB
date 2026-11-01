# AHEADDB Live consistency contract

Live is an optional PostgreSQL owned-source prediction layer around the existing
AHEADDB statistical engine. The original library, local API and numerical models
remain available. Install the `live` extra for the durable service and HTTP SDK.
This document defines the implemented consistency boundary. The
[verification record](live-verification.md) separates executed local checks,
workload measurements and remaining release gates; none is a reliability certification.

## Source ownership and coherent boundaries

Only the dedicated Live schema is written. This is an owned versioned source,
not a CDC adapter for existing application tables. Production CDC or schema
changes need separate authorization. Use a dedicated database role and namespace;
reserved PostgreSQL schemas are rejected and unrelated namespaces are preserved.

A source edit holds one revision-row lock until commit. It closes prior row
versions, writes replacement rows, validates the complete candidate snapshot,
records the outbox receipt and enqueues all declared views in one transaction.
Revisions therefore follow the serialization of committed owned edits. Request
IDs, job sequence IDs, event timestamps and allocation order are never source
progress cursors. Aborted transactions do not advance the revision. The same
request ID and exact contents return the same receipt; conflicting contents fail.

Live persists primary and foreign keys as strings. Source edits convert finite
JSON scalar identifiers with Python's `str` rule before storage (`1` → `"1"`,
`true` → `"True"`, `1.0` → `"1.0"`); null, array, object and nonfinite identifiers
are rejected. Bootstrap persists the validated Snapshot's string identifiers.
This prevents later pandas column inference from changing an unchanged row's
owner. Request deduplication still compares the exact submitted edit contents.

Pre-release databases/backups containing non-string persisted identifiers need an
explicit offline ownership migration. Live rejects edits over noncanonical current
rows and rejects purge/backup/restore when any retained identifier is noncanonical.
It preserves the source and refuses import rather than guessing that `"1"` and
`"1.0"` identify the same entity. The current release does not provide that legacy
migration. Normal purge/restore continues for canonical sources and backups.

A job names a source revision. Version intervals reconstruct precisely that
revision, including deletions and owner moves. The claim transaction records an
immutable snapshot and lease before computation. No old cursor is applied to
mutable current rows. Bootstrap and view registration share the revision lock;
a view's first queued revision is the current committed head at registration.
An edit before registration is included in that first snapshot; a later edit
creates another job. There is no independent bootstrap-to-stream watermark.

## Clocks and freshness

These times have distinct meanings:

| Field | Meaning |
|---|---|
| Event occurred_at | When a domain event happened |
| Event available_at | When that event was available for the temporal learning task |
| Prediction cutoff | The explicit information instant for feature/window selection |
| Revision | Logical committed source boundary, unrelated to event timestamps |
| observed_at | Worker observation of the identified source snapshot |
| source_as_of | Conservative instant when the covered owned source state was verified |
| published_at | Wall-clock observation inside the publication transaction; a lower bound on commit, not source freshness |
| processed_revision | Minimum published source revision across current view definitions |

At read time Live locks and checks the same owned source revision row used by
writers. If that revision still equals the generation revision and its definition
matches, the read has new evidence that the prediction covers the entire committed
owned source. It may renew source_as_of from that verification, leaving publication
and original snapshot-observation times intact. Source silence therefore does not
require unnecessary model work. This guarantee ends at the owned source; it says
nothing about an external upstream feed that has stopped ingesting.

Known uncovered edits, replaced models/cutoffs and old observations are stale.
Clock discontinuities are unknown. Both require explicit `allow_stale=true`;
otherwise reads fail with 503. A database/network outage is unavailable, never a
fresh cached success. Latest-published and retained-generation reads use the same
checks. `max_age_seconds` adds a caller's stricter source-observation bound.
A fixed prediction cutoff stays fixed until a new view definition advances it.
Fresh source coverage does not imply the forecast cutoff is appropriate for today.

Every DB connection commits clock evidence separately from the requested operation.
A backward DB wall-clock movement, or a wall-vs-process-monotonic discrepancy above
two seconds while a process is running, latches unknown coverage. Operator clock
acknowledgement invalidates publication pointers and queues new work. Leases use
the DB clock and monotonically increasing fences; a clock jump never authorizes an
expired worker to overwrite a newer claimant. A forward jump during complete
process downtime cannot be distinguished from elapsed downtime using these clocks;
leases/deadlines expire conservatively. A new owned-source verification can establish
a new observation. There is no external trusted-clock SLA.

## Models, reuse and generations

Models are safe declarative training recipes plus a retained training snapshot,
not executable serialized files. A recovered process deterministically refits the
recipe using pinned dependencies and checks a canonical model identity. The Live
identity includes learned state, task, training data, feature preprocessing,
numerical runtime and compiler/model implementation hashes. An incompatible
runtime requires explicit replacement registration. Model replay costs count as
recovery/refresh costs. No untrusted pickle or arbitrary Python/SQL is loaded.

The existing Engine computes dependency differences across full snapshot indexes,
invalidates both owners on moves, and handles insert/update/delete and late rows.
All event relations participate in context reuse decisions. Entity availability,
history, cutoff, schema, model and preprocessing are semantic dependencies.
Changing the cutoff or model conservatively rebuilds; unsupported schema changes
fail. Expiring windows are handled by advancing the declared cutoff, not by silently
changing the task on wall-clock ticks. Provenance describes invalidation/input
membership, never causal effects.

A worker claims bounded jobs with a lease and per-view monotonic fence. Publication
checks every lease, fence, definition and purge epoch, then atomically writes the
complete selected generation, pointers, job completion/supersession and minimum
consumer progress. An older source revision cannot replace a newer publication.
Expired/cancelled workers are fenced. Retried computation may repeat; only
publication is idempotent at this transaction boundary. There is no universal
exactly-once external-effect claim.

Views bundled into one generation publish mutually atomically. Separately
requested latest views can return different generation/source revisions. Clients
must inspect the returned boundary; cross-view latest reads are not a mutually
atomic API. Retained reads never silently fall forward to a different generation.

## Planner and bounds

Full rebuild and FIFO process the oldest durable job. FIFO may use the original
incremental Engine when its process still retains a compatible prior context.
Debouncing coalesces newer source revisions after a quiet period with a maximum
wait. The budget planner preserves the earliest outstanding age/deadline, uses an
interpretable conservative moving average of observed cost, and prioritizes aged
work before deadline slack. Compatible tasks share source revision, schema,
history, cutoff and preprocessing, while running their own models. Superseded jobs
are completed only when a covering newer source generation commits.

Default bounds: 10,000 pending/running jobs (projected transaction size checked),
128 views, 100,000 source rows/64 MiB stored source payload, three attempts per job,
30-second leases (maximum 300), four cached engine groups and 128 cached models.
Backpressure rejects the whole source edit. Poison work becomes failed, keeps
coverage stale, and needs correction/retry or a covering later valid revision.
Limits are safety bounds, not measured throughput guarantees. A single slow model
can exceed a non-renewed lease; choose an appropriate bounded lease or fail safely.

Retention protects active pointers and job source versions. Purging an entity
removes all retained versions of its events, including moved events, and invalidates
all models and generations because training can depend on every entity. Refitting
is then required. Old retained generation IDs cannot retrieve purged data. Backups
are private source artifacts; pre-purge backups need an external current purge
ledger replayed before any service is started. The portable restore path locks a source-bound ledger through its import commit and replays purge intent before importing rows. The owner must supply the current external ledger; stale ledger copies cannot prove later deletions. Restores are offline cutovers, without automatic purge propagation to other live copies.

## Foundations and alternatives

PostgreSQL's [isolation documentation](https://www.postgresql.org/docs/current/transaction-iso.html)
warns that sequences are not transactional; its [logical decoding documentation](https://www.postgresql.org/docs/current/logicaldecoding-explanation.html)
requires duplicate handling after crashes and a coordinated snapshot transition.
The owned revision lock avoids claiming production CDC correctness.
[Hypothesis](https://hypothesis.readthedocs.io/) supplies generated test sequences.

[Materialize](https://materialize.com/docs/fundamentals/concepts/views/) already
maintains durable incremental SQL views and supports explicit
[bounded-staleness reads](https://materialize.com/docs/serve-results/isolation-level/).
[dbt State](https://docs.getdbt.com/docs/deploy/dbt-state-about) already makes reuse
and freshness-aware rebuild decisions. [Feast](https://docs.feast.dev/getting-started/concepts/point-in-time-joins)
already supports point-in-time feature retrieval. Live does not claim those ideas
as new. Its bounded experiment is maintaining this existing statistical engine's
entity predictions through owned-source edits and worker failure. Whether its
planner improves end-to-end work or deadlines is an experimental question.

# AHEADDB Live quickstart

Use Python 3.12, uv and PostgreSQL 15 or newer. Everything runs locally with the
existing CPU statistical engine. No API key, model download or paid service is
needed. Live adds an owned PostgreSQL source and durable workers; the original
library/local inspector remain available.

## A dedicated disposable PostgreSQL source

Use an existing dedicated development database, or create a fresh loopback-only
cluster with installed PostgreSQL binaries on PATH:

```sh
AHEAD_PG_WORK=$(mktemp -d "${TMPDIR:-/tmp}/ahead-live.XXXXXX")
initdb -D "$AHEAD_PG_WORK/data" -A trust --no-locale --encoding=UTF8
pg_ctl -D "$AHEAD_PG_WORK/data" -l "$AHEAD_PG_WORK/postgres.log" \
  -o "-h 127.0.0.1 -p 55439 -k $AHEAD_PG_WORK" start
export AHEAD_LIVE_DSN="postgresql://${USER}@127.0.0.1:55439/postgres"
```

Choose a free local port if 55439 is already in use. This creates only an isolated
synthetic development source. Ordinary application tables are never modified or
automatically enrolled in CDC. Keep database credentials in your own connection
configuration; do not put them in recipes, examples or source control.

## Install and run the two-feature application

The pinned release checkout includes the SDK example and lockfile:

```sh
git clone --branch v0.3.0 https://github.com/nazeeh111/AHEADDB.git
cd AHEADDB
uv sync --frozen --extra live
uv run --no-sync ahead live demo
uv run --no-sync ahead live worker
```

In a second terminal, with the same `AHEAD_LIVE_DSN`:

```sh
uv run --no-sync ahead live serve
```

In a third terminal, which needs no database connection:

```sh
uv run --no-sync python examples/live_desk/app.py
```

Open http://127.0.0.1:8791. Return probability supports customer follow-up;
expected gross revenue supports stock/cash planning. Both use shared orders but
separate outcomes/models. Record an order, stop the worker, record another, and
check current estimates. Current reads fail while uncovered changes remain.
Older values require the explicit checkbox. Restart the worker to recover and
inspect the covered source revision. No outreach or purchases occur.

This fixture uses a July 15, 2024 information cutoff and synthetic historical events.
Source verification and publication happen when you run the service. The cutoff
stays fixed until you explicitly publish a replacement definition.

## Declare and evaluate your own task

Use an empty dedicated namespace for the independent developer walkthrough:

```sh
uv run --no-sync ahead live init --namespace developer_example
uv run --no-sync ahead live serve --namespace developer_example --port 8792
# another terminal with the same database setting
uv run --no-sync ahead live worker --namespace developer_example
# client terminal, no database setting needed
uv run --no-sync python examples/live_developer.py --base-url http://127.0.0.1:8792
```

The client generates its own accounts/activity rows, declares keys and both clocks,
evaluates chronological folds with prior, historical/persistence and histogram
baselines, registers safe model recipes, publishes two views, changes an input,
and waits for the acknowledged source revision. It checks that model/cutoff stay
fixed while source identity and the new generation remain explicit. The numerical
change is synthetic integration evidence, not real-data predictive validation.

For data-only JSON automation, use `ahead live bootstrap`, `evaluate`, `model`,
`view`, `edit`, `read` and `status`. Their request types are in
`aheaddb.live.contracts` and the [v1 OpenAPI contract](../contracts/v1/openapi.json).
The original `backtest`, `fit`, `Compiler`, `Engine` and `Snapshot` APIs are unchanged.

## Read contracts

```python
from aheaddb.live.client import Client
from aheaddb.live import Unavailable

with Client() as client:
    try:
        answer = client.read("return_14d", max_age_seconds=30)
        print(answer.coverage.source_id, answer.coverage.revision)
        print(answer.model_version, answer.prediction_cutoff, answer.generation)
    except Unavailable:
        print("No answer meets the requested freshness contract")
```

`generation=...` selects a retained generation. `allow_stale=True` is required for
stale/unknown results. It is never enabled silently. Source verification can renew
coverage only after checking the owned source revision under its writer lock.
The `published_at` wall clock is a lower bound from inside the publication
transaction, explicitly identified by `publication_time_semantics`.

Separate latest-view reads are not mutually atomic. Check source IDs/revisions
before combining them; compatible views published in one generation share its
transaction. After a write, require `coverage.revision >=` the edit receipt and
the same source ID. A restored old backup has its captured boundary, not knowledge
of later writes. Continuous WAL/PITR disaster recovery is outside this local layer.

## Retention and recovery

`uv run --no-sync ahead live retain --per-view 5` removes old generations while protecting active
pointers, pending/failed source versions and training snapshots. Failed jobs count
toward admission bounds. Correct the failure then `uv run --no-sync ahead live retry TASK`; a newer complete
generation can supersede older pending work. Cancellation fences in-flight work.
Schema changes require a new schema/task/model, not silent reinterpretation.

For portable backups, choose private paths and retain the external purge ledger:

```sh
mkdir -p .runtime/backups
uv run --no-sync ahead live backup .runtime/backups/private-backup.json .runtime/backups/private-purges.jsonl
uv run --no-sync ahead live purge CUSTOMER_ID .runtime/backups/private-purges.jsonl
uv run --no-sync ahead live restore .runtime/backups/private-backup.json .runtime/backups/private-purges.jsonl --namespace restored_source
```

Live stores identifiers as strings. Finite scalar edit IDs are normalized before
storage; null/complex IDs are rejected. Pre-release sources/backups with raw
numeric or boolean persisted IDs require an explicit offline ownership migration.
Maintenance refuses them without deleting/importing rows, including legacy values
in retained history. It does not guess equivalence between IDs such as `"1"` and
`"1.0"`. Keep the original source/backup; that migration is outside this release.

Backup creation preserves an existing file instead of overwriting it. Restore
requires a fresh namespace and the current source-bound ledger; it applies purge
intent before importing rows. A purge invalidates every model/generation because
training can depend on the entity, so refitting is required. Old ledger copies
cannot prove later deletions. Restores are offline cutovers, not automatic purge
propagation to other live copies. After checking the restored clock and boundary,
use `uv run --no-sync ahead live acknowledge-clock` and re-establish required views. Do not expose a restored
source before completing this owner-controlled workflow.

Stop the disposable cluster when finished:

```sh
pg_ctl -D "$AHEAD_PG_WORK/data" stop
```

Sources, API, SDK and example are local. Production authentication, external CDC,
rolling retraining, high availability and unbounded data are outside v1.

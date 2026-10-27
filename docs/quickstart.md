# Build a local prediction task

Use Python 3.12 (3.13 is allowed by the core package; release evidence uses3.12). The saved `uv.lock` pins the complete environment. No GPU, API key or network access is needed after installation to run the demo.

```sh
git clone https://github.com/nazeeh111/AHEADDB.git
cd AHEADDB
uv sync --frozen
uv run --no-sync ahead demo run/signaldesk
uv run --no-sync ahead serve
```

Open http://127.0.0.1:8787. `ahead demo` refuses to overwrite an existing example; reuse its project or choose a fresh directory. The CLI prints JSON for scripts:

```sh
uv run --no-sync ahead inspect
uv run --no-sync ahead plan
uv run --no-sync ahead backtest
uv run --no-sync ahead predict --csv run/predictions.csv
uv run --no-sync ahead provenance C0000
```

CSV export opens a fresh file exclusively to protect previous exports. To compare a replacement snapshot in one command: `ahead refresh /path/to/new.duckdb --project run/signaldesk/ahead.json`. For a continuously running application, call `Engine.refresh(new_snapshot)` or `/api/refresh` after an atomic replacement of the configured snapshot. There is no hidden polling, database trigger or production write.

## Declare the schema and task

```python
from aheaddb import Event, Schema, Outcome, Task, Snapshot, Compiler, Engine, fit

schema = Schema(
    entity_table="customers", entity_key="customer_id", available_at="created_available_at",
    events=(Event(table="orders", key="order_id", entity_key="customer_id",
                  occurred_at="ordered_at", available_at="ingested_at", values=("amount",)),),
)
task = Task(name="buys_30d", kind="binary", outcome=Outcome(table="orders"),
            horizon_days=30, history_days=90, label_delay_days=2, metric="roc_auc")
snapshot = Snapshot.duckdb("snapshot.duckdb", schema)
print(Compiler(snapshot).plan(task, "2024-10-01"))
model = fit(snapshot, task, ["2024-03-01", "2024-04-01", "2024-05-01"], "2024-07-01")
engine = Engine(snapshot)
predictions = engine.materialize(task, model, "2024-10-01")
receipt = engine.refresh(Snapshot.duckdb("replacement.duckdb", schema))
```

For numerical forecasting use `kind="regression"`, a count or `Outcome(table="orders", aggregation="sum", column="amount")`, and `metric="mae"` or `"rmse"`. Binary sum tasks also support a strict threshold. Count/sum are the supported future outcomes; arbitrary Python/SQL label expressions, recommendations and multihop relationships are not supported. Primary keys must be unique and non-null, foreign keys valid, clocks non-null and numeric values finite when observed. Use `Snapshot.parquet({"customers":"customers.parquet", "orders":"orders.parquet"}, schema)` for explicit local Parquet snapshots.

Train only on mature labels. The supplied `as_of` must reflect a truly complete source observation interval. Naive times explicitly mean UTC. Zero events means zero only for complete event logs; missing observed numerical outcomes fail instead of becoming invented zeros. See [temporal semantics](architecture.md) and [actual evaluation assumptions](data.md).

Use `backend="baseline"` or `"histogram"` with `fit`; backtests compare both. A project JSON may set `"backend_by_task": {"buys_30d":"baseline"}` to serve a simpler model. Choose using appropriate validation, not the final held-out tests. Histogram boosting is not universally better: see [saved failures](data.md).

## Runtime and diagnostics

The core uses one numerical thread for fitting/prediction and no pretrained weights. Memory use includes in-RAM snapshots, contexts and views. DuckDB ingestion is bounded to 512 MB and 2 threads with a default 2 million-row limit, but that is not a total Python-process memory cap. The SDK is suitable for small local databases; large/sensitive workloads require additional operational work.

`Engine.inspect()` provides versions, warnings, a simple training feature-range screen and counts of rows without history. These are visible limitations, not statistical drift detection or automatic confidence guarantees. Binary outputs are raw probabilities; inspect Brier/reliability in backtests. Regression outputs are unconstrained, so a nonnegative business target can still receive an invalid negative estimate. Handle domain constraints in the consuming application and test them explicitly.

Models and caches are process-local; restart refits from the manifest. No untrusted pickle loading is offered. Schema changes require a new engine and retraining. Corrected source history must preserve information availability; overwriting rows cannot reconstruct their historical former values. See [architecture](architecture.md), [agent integration](agents.md), [research](research.md) and [licenses](licenses.md).

## Read-only PostgreSQL snapshots

The optional `postgres` extra reads through a caller-owned psycopg3 connection. AHEADDB accepts no DSN or credentials of its own and changes no source schema. Start with an idle connection you have explicitly opened using your application's existing connection configuration:

```python
# connection is a caller-owned, idle psycopg3 Connection.
snapshot = Snapshot.postgres(connection, schema)
engine.refresh(snapshot)
```

Each read runs in one `REPEATABLE READ READ ONLY` transaction with a 10-second statement timeout and 1-second lock timeout, bounded by the same row cap. It leaves the connection idle on success or failure. Qualified schema selection is the caller's `search_path` configuration; table fields are quoted single identifiers. Only initial snapshot ingestion is provided: no production triggers, CDC, credential management or automatic connections. The CI integration uses a fresh PostgreSQL 16 service with disposable data and deliberately attempts a persistent write inside the adapter transaction to verify rejection. No user's PostgreSQL server is contacted by tests.

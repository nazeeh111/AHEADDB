"""Owned PostgreSQL source, versioned rows, outbox and publication transaction.

All writes lock one revision row. Revisions order *commits* because that lock is
held through commit; UUID/request IDs are never progress cursors. No production
schema/CDC adapter is installed. SQL identifiers are composed, values are bound.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..compiler import Compiler
from ..modeling import FittedModel, backtest, fit
from ..snapshot import Snapshot
from ..spec import Schema, digest, timestamp
from .contracts import (
    Answer,
    Coverage,
    Edit,
    EvaluationReport,
    EvaluationRequest,
    ModelRecipe,
    ViewDefinition,
)
from .model import replay_model, runtime_versions

DDL = """
CREATE TABLE IF NOT EXISTS meta (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 version integer NOT NULL, revision bigint NOT NULL DEFAULT 0,
 processed_revision bigint NOT NULL DEFAULT 0,
 schema_doc jsonb, last_clock timestamptz, clock_unknown boolean NOT NULL DEFAULT false,
 purge_epoch bigint NOT NULL DEFAULT 0
);
INSERT INTO meta(singleton,version) VALUES(true,1) ON CONFLICT DO NOTHING;
ALTER TABLE meta ADD COLUMN IF NOT EXISTS source_id text NOT NULL DEFAULT gen_random_uuid()::text;
ALTER TABLE meta ADD COLUMN IF NOT EXISTS ledger_hash text NOT NULL DEFAULT '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945';
ALTER TABLE meta ADD COLUMN IF NOT EXISTS ledger_count integer NOT NULL DEFAULT 0;
ALTER TABLE meta ADD COLUMN IF NOT EXISTS unlogged_purge boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS source_rows (
 table_name text NOT NULL, row_key text NOT NULL, valid_from bigint NOT NULL,
 valid_to bigint, payload jsonb NOT NULL, PRIMARY KEY(table_name,row_key,valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS current_row ON source_rows(table_name,row_key) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS row_revision ON source_rows(valid_from,valid_to);
CREATE TABLE IF NOT EXISTS outbox (
 revision bigint PRIMARY KEY, request_id text UNIQUE NOT NULL,
 request_hash text NOT NULL, changes jsonb NOT NULL,
 authored_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS snapshots (
 id text PRIMARY KEY, revision bigint NOT NULL, data_version text NOT NULL,
 payload jsonb NOT NULL, source_as_of timestamptz NOT NULL,
 observed_at timestamptz NOT NULL, purge_epoch bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS models (
 id text PRIMARY KEY, recipe jsonb NOT NULL, snapshot_id text NOT NULL REFERENCES snapshots(id),
 model_version text NOT NULL, runtime jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS views (
 name text PRIMARY KEY, definition jsonb NOT NULL, definition_hash text NOT NULL,
 task jsonb NOT NULL, fence bigint NOT NULL DEFAULT 0, generation text,
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS jobs (
 id bigserial PRIMARY KEY, view_name text NOT NULL REFERENCES views(name) ON DELETE CASCADE,
 revision bigint NOT NULL, definition_hash text NOT NULL,
 queued_at timestamptz NOT NULL DEFAULT clock_timestamp(), deadline timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'pending', token bigint, lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0, error text, snapshot_id text,
 UNIQUE(view_name,revision,definition_hash)
);
CREATE INDEX IF NOT EXISTS pending_jobs ON jobs(state,queued_at);
CREATE TABLE IF NOT EXISTS generations (
 id text NOT NULL, view_name text NOT NULL, revision bigint NOT NULL,
 definition_hash text NOT NULL, snapshot_id text NOT NULL REFERENCES snapshots(id),
 answer jsonb NOT NULL, published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(id,view_name)
);
CREATE TABLE IF NOT EXISTS costs (
 context text PRIMARY KEY, seconds double precision NOT NULL, samples bigint NOT NULL
);
"""


class Unavailable(RuntimeError):
    """The requested read cannot meet its declared freshness contract."""


class Conflict(RuntimeError):
    """An idempotency key, schema or fenced publication conflicted."""


def encode_snapshot(snapshot: Snapshot) -> dict:
    def scalar(value):
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if hasattr(value, "item"):
            return value.item()
        raise TypeError("Unsupported source scalar")
    tables = {name: frame.astype(object).where(pd.notna(frame), None).to_dict("records")
              for name, frame in snapshot._tables.items()}
    # Python's JSON encoder round-trips binary64 values. pandas.to_json defaults
    # to ten decimal places and would silently change the model's source data.
    return json.loads(json.dumps({"schema": snapshot.schema.model_dump(), "tables": tables},
                                 default=scalar, allow_nan=False))


def decode_snapshot(payload: dict) -> Snapshot:
    schema = Schema.model_validate(payload["schema"])
    tables = {name: pd.DataFrame(rows) for name, rows in payload["tables"].items()}
    # Preserve declared columns even when a relation is empty.
    for event in schema.events:
        columns = [event.key, event.entity_key, event.occurred_at, event.available_at, *event.values]
        if tables[event.table].empty:
            tables[event.table] = pd.DataFrame({c: pd.Series(dtype="float64" if c in event.values else "object")
                                                for c in columns})
        for column in event.values:
            tables[event.table][column] = pd.to_numeric(tables[event.table][column], errors="raise")
    if tables[schema.entity_table].empty:
        tables[schema.entity_table] = pd.DataFrame(columns=[schema.entity_key, schema.available_at])
    for name, columns in [(schema.entity_table, [schema.available_at]), *[(e.table, [e.occurred_at, e.available_at]) for e in schema.events]]:
        for column in columns:
            tables[name][column] = pd.to_datetime(tables[name][column], utc=True, format="ISO8601", errors="raise")
    return Snapshot(tables, schema)



def identifier_columns(schema: Schema) -> dict[str, tuple[str, ...]]:
    return {schema.entity_table: (schema.entity_key,),
            **{event.table: (event.key, event.entity_key) for event in schema.events}}


def canonical_identifier(value: Any) -> str:
    if not isinstance(value, (str, int, float, bool)) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("Identifiers must be non-null finite JSON scalars")
    return str(value)


def require_canonical_rows(rows: list[dict], schema: Schema) -> None:
    columns = identifier_columns(schema)
    for row in rows:
        names = columns.get(row["table_name"])
        payload = row["payload"]
        if (not names or not isinstance(payload, dict)
                or any(not isinstance(payload.get(name), str) for name in names)
                or payload.get(names[0]) != row["row_key"]):
            raise Conflict("Legacy identifiers require explicit offline migration; no source rows imported or removed")


class Store:
    """DSN stays in caller memory. Callers must supply a dedicated owned database/schema."""
    def __init__(self, dsn: str, namespace: str = "ahead_live", max_backlog: int = 10000,
                 max_rows: int = 100000, max_source_bytes: int = 64 * 1024 * 1024):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", namespace) or namespace in {"public", "information_schema"} or namespace.startswith("pg_"):
            raise ValueError("Invalid owned namespace")
        self._dsn = dsn
        self.namespace = namespace
        self.max_backlog = max_backlog
        self.max_rows, self.max_source_bytes = max_rows, max_source_bytes
        self._clock_sample: tuple[float, float] | None = None

    @contextmanager
    def connect(self) -> Iterator[Any]:
        with psycopg.connect(self._dsn, autocommit=True, row_factory=dict_row,
                             connect_timeout=3) as conn:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.namespace)))
            conn.execute("SET TIME ZONE 'UTC'")
            conn.execute("SET statement_timeout='10s'")
            conn.execute("SET lock_timeout='3s'")
            relation = conn.execute("SELECT to_regclass('meta') AS name").fetchone()
            if relation and relation["name"]:
                # Clock evidence must survive even if the requested operation
                # subsequently rejects a read or rolls back an invalid edit.
                with conn.transaction():
                    self._observe_clock(conn)
            yield conn

    def migrate(self) -> None:
        """Transactional, version-checked creation of this owned schema only."""
        with self.connect() as conn, conn.transaction():
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.namespace)))
            exists = conn.execute("SELECT to_regclass('meta') AS name").fetchone()["name"]
            if not exists and conn.execute("SELECT count(*) n FROM information_schema.tables WHERE table_schema=%s",
                                           (self.namespace,)).fetchone()["n"]:
                raise Conflict("Namespace contains unrelated tables; use a fresh owned namespace")
            if exists and conn.execute("SELECT version FROM meta").fetchone()["version"] != 1:
                raise Conflict("Unsupported Live schema; migration rolled back")
            conn.execute(DDL)

    def _observe_clock(self, conn) -> None:
        meta = conn.execute("SELECT * FROM meta WHERE singleton FOR UPDATE").fetchone()
        if not meta or meta["version"] != 1:
            raise Conflict("Unsupported Live schema")
        now = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
        wall, mono = now.timestamp(), time.monotonic()
        jump = meta["last_clock"] is not None and now < meta["last_clock"]
        if self._clock_sample:
            before_wall, before_mono = self._clock_sample
            jump |= abs((wall - before_wall) - (mono - before_mono)) > 2
        self._clock_sample = wall, mono
        conn.execute("UPDATE meta SET last_clock=%s, clock_unknown=clock_unknown OR %s", (now, jump))

    def _lock(self, conn) -> dict:
        meta = conn.execute("SELECT * FROM meta WHERE singleton FOR UPDATE").fetchone()
        if not meta or meta["version"] != 1:
            raise Conflict("Unsupported Live schema")
        meta["now"] = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
        return meta

    def acknowledge_clock(self) -> None:
        """Operator assertion: the DB clock is stable again; force all views to refresh."""
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            conn.execute("UPDATE meta SET clock_unknown=false,last_clock=clock_timestamp()")
            conn.execute("UPDATE views SET fence=fence+1,generation=NULL")
            conn.execute("UPDATE jobs SET state='cancelled' WHERE state IN ('pending','running','failed')")
            # New definition hash prevents conflict with cancelled unique keys.
            for view in conn.execute("SELECT * FROM views").fetchall():
                h = digest([view["definition_hash"], str(uuid.uuid4())])
                conn.execute("UPDATE views SET definition_hash=%s WHERE name=%s", (h, view["name"]))
            self._update_progress(conn)
            self._enqueue(conn, meta["revision"])
        self._clock_sample = None

    def _snapshot(self, conn, revision: int, meta: dict) -> tuple[str, Snapshot]:
        if not meta["schema_doc"]:
            raise Unavailable("Source has not been bootstrapped")
        tables: dict[str, list] = {meta["schema_doc"]["entity_table"]: []}
        tables.update({e["table"]: [] for e in meta["schema_doc"]["events"]})
        size = conn.execute("SELECT count(*) n,COALESCE(sum(octet_length(payload::text)),0) bytes FROM source_rows "
                            "WHERE valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)", (revision, revision)).fetchone()
        if size["n"] > self.max_rows or size["bytes"] > self.max_source_bytes:
            raise Unavailable("Owned source exceeds configured row/byte limit")
        rows = conn.execute("SELECT table_name,payload FROM source_rows WHERE valid_from<=%s "
                            "AND (valid_to IS NULL OR valid_to>%s) ORDER BY table_name,row_key",
                            (revision, revision)).fetchall()
        for row in rows:
            tables[row["table_name"]].append(row["payload"])
        snapshot = decode_snapshot({"schema": meta["schema_doc"], "tables": tables})
        observed = meta["now"]
        # If there is a newer revision, its pre-commit authored_at is a conservative
        # point when the committed source was still at this revision. Never stamp an
        # old revision with today's time and claim today's source coverage.
        successor = conn.execute("SELECT authored_at FROM outbox WHERE revision=%s", (revision + 1,)).fetchone()
        as_of = successor["authored_at"] if successor else observed
        sid = digest([revision, snapshot.version, meta["purge_epoch"]])
        conn.execute("INSERT INTO snapshots VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (sid, revision, snapshot.version, Jsonb(encode_snapshot(snapshot)),
                      as_of, observed, meta["purge_epoch"]))
        # A repeated source revision can be observed more recently without changing
        # snapshot contents. Publication uses the claim observation, not this cache's age.
        return sid, snapshot

    def snapshot(self) -> tuple[str, Snapshot]:
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            return self._snapshot(conn, meta["revision"], meta)

    def bootstrap(self, snapshot: Snapshot) -> int:
        payload = encode_snapshot(snapshot)
        if (sum(len(rows) for rows in payload["tables"].values()) > self.max_rows
                or len(json.dumps(payload).encode()) > self.max_source_bytes):
            raise Unavailable("Owned source exceeds configured row/byte limit")
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            if meta["schema_doc"] is not None:
                raise Conflict("Owned source already exists; bootstrap never replaces it")
            payload = encode_snapshot(snapshot)
            conn.execute("UPDATE meta SET schema_doc=%s,revision=1,processed_revision=1", (Jsonb(payload["schema"]),))
            source_rows: list[tuple[str, str, Jsonb]] = []
            for name, rows in payload["tables"].items():
                key = snapshot.schema.entity_key if name == snapshot.schema.entity_table else next(
                    e.key for e in snapshot.schema.events if e.table == name)
                source_rows.extend((name, str(row[key]), Jsonb(row)) for row in rows)
            with conn.cursor() as cursor:
                cursor.executemany("INSERT INTO source_rows VALUES(%s,%s,1,NULL,%s)", source_rows)
            conn.execute("INSERT INTO outbox(revision,request_id,request_hash,changes) VALUES(1,'bootstrap',%s,'[]')",
                         (snapshot.version,))
        return 1

    def _update_progress(self, conn) -> None:
        progress = conn.execute("SELECT COALESCE(min(COALESCE(g.revision,0)),(SELECT revision FROM meta)) AS revision "
                                "FROM views v LEFT JOIN generations g ON g.id=v.generation AND g.view_name=v.name "
                                "AND g.definition_hash=v.definition_hash").fetchone()["revision"]
        conn.execute("UPDATE meta SET processed_revision=%s", (progress,))

    def _enqueue(self, conn, revision: int) -> None:
        pending = conn.execute("SELECT count(*) n FROM jobs WHERE state IN ('pending','running','failed')").fetchone()["n"]
        needed = conn.execute("SELECT count(*) n FROM views v WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE "
                              "j.view_name=v.name AND j.revision=%s AND j.definition_hash=v.definition_hash)",
                              (revision,)).fetchone()["n"]
        if needed and pending + needed > self.max_backlog:
            raise Unavailable("Backlog limit reached; transaction rolled back")
        conn.execute("INSERT INTO jobs(view_name,revision,definition_hash,deadline) "
                     "SELECT name,%s,definition_hash,clock_timestamp()+"
                     "((definition->>'freshness_seconds')::double precision * interval '1 second') FROM views "
                     "ON CONFLICT DO NOTHING", (revision,))

    def edit(self, edit: Edit, fault=None) -> int:
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            previous = conn.execute("SELECT revision,request_hash FROM outbox WHERE request_id=%s",
                                    (edit.request_id,)).fetchone()
            request_hash = digest(edit.model_dump())
            if previous:
                if previous["request_hash"] != request_hash:
                    raise Conflict("Request ID reused for different contents")
                return previous["revision"]
            if not meta["schema_doc"]:
                raise Unavailable("Source has not been bootstrapped")
            if conn.execute("SELECT count(*) AS n FROM jobs WHERE state IN ('pending','running','failed')").fetchone()["n"] >= self.max_backlog:
                raise Unavailable("Backlog limit reached; edit rolled back")
            schema = Schema.model_validate(meta["schema_doc"])
            self._require_canonical_identifiers(conn, schema, current_only=True)
            keys = {schema.entity_table: schema.entity_key, **{e.table: e.key for e in schema.events}}
            columns = identifier_columns(schema)
            revision = meta["revision"] + 1
            dependencies = []
            seen = set()
            for mutation in edit.mutations:
                if mutation.table not in keys or (mutation.table, mutation.key) in seen:
                    raise ValueError("Unknown table or duplicate row in transaction")
                seen.add((mutation.table, mutation.key))
                row = None if mutation.row is None else dict(mutation.row)
                if row is not None:
                    for name in columns[mutation.table]:
                        row[name] = canonical_identifier(row.get(name))
                    if row[keys[mutation.table]] != mutation.key:
                        raise ValueError("Mutation key differs from row primary key")
                old = conn.execute("UPDATE source_rows SET valid_to=%s WHERE table_name=%s AND row_key=%s "
                                   "AND valid_to IS NULL RETURNING payload", (revision, mutation.table, mutation.key)).fetchone()
                if row is not None:
                    conn.execute("INSERT INTO source_rows VALUES(%s,%s,%s,NULL,%s)",
                                 (mutation.table, mutation.key, revision, Jsonb(row)))
                dependencies.append({"table": mutation.table, "key": mutation.key,
                                     "operation": "delete" if mutation.row is None else "update" if old else "insert"})
            conn.execute("UPDATE meta SET revision=%s", (revision,))
            conn.execute("INSERT INTO outbox(revision,request_id,request_hash,changes) VALUES(%s,%s,%s,%s)",
                         (revision, edit.request_id, request_hash, Jsonb(dependencies)))
            meta["revision"] = revision
            # Validate the whole candidate including owner changes and entity deletes.
            # This cost is intentional and included in source-edit measurements.
            self._snapshot(conn, revision, meta)
            self._enqueue(conn, revision)
            if fault:
                fault("edit_before_commit")
        if fault:
            fault("edit_after_commit")
        return revision

    def evaluate(self, request: EvaluationRequest) -> EvaluationReport:
        sid, snapshot = self.snapshot()
        backends = tuple(dict.fromkeys(("baseline", "histogram", "persistence", request.recipe.backend)))
        report = backtest(snapshot, request.recipe.task, list(request.backtest_cutoffs),
                          request.labels_as_of, backends=backends)
        plan = Compiler(snapshot).plan(request.recipe.task, request.prediction_cutoff)
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            retained = conn.execute("SELECT revision,purge_epoch FROM snapshots WHERE id=%s", (sid,)).fetchone()
            if not retained or retained["purge_epoch"] != meta["purge_epoch"]:
                raise Conflict("Source purged during evaluation")
            return EvaluationReport(source_id=meta["source_id"], revision=retained["revision"], snapshot_id=sid,
                                    data_version=snapshot.version, plan=plan, report=report)

    def register_model(self, recipe: ModelRecipe) -> str:
        # Normalize JSON numeric defaults before hashing/replaying the recipe.
        recipe = ModelRecipe.model_validate_json(recipe.model_dump_json())
        sid, snapshot = self.snapshot()
        model = replay_model(fit(snapshot, recipe.task, list(recipe.training_cutoffs), recipe.training_as_of, recipe.backend))
        runtime = runtime_versions()
        mid = digest([recipe.model_dump(), sid, model.version, runtime])
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            retained = conn.execute("SELECT purge_epoch FROM snapshots WHERE id=%s", (sid,)).fetchone()
            if not retained or retained["purge_epoch"] != meta["purge_epoch"]:
                raise Conflict("Source purged during model fitting")
            conn.execute("INSERT INTO models VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (mid, Jsonb(recipe.model_dump()), sid, model.version, Jsonb(runtime)))
        return mid

    def load_model(self, model_id: str) -> FittedModel:
        with self.connect() as conn:
            row = conn.execute("SELECT m.*,s.payload FROM models m JOIN snapshots s ON s.id=m.snapshot_id WHERE m.id=%s",
                               (model_id,)).fetchone()
        if not row:
            raise Unavailable("Model artifact is unavailable (possibly purged)")
        if row["runtime"] != runtime_versions():
            raise Conflict("Pinned model runtime differs; explicitly register a replacement")
        recipe = ModelRecipe.model_validate(row["recipe"])
        model = replay_model(fit(decode_snapshot(row["payload"]), recipe.task, list(recipe.training_cutoffs),
                                 recipe.training_as_of, recipe.backend))
        if model.version != row["model_version"]:
            raise Conflict("Model replay differs from registered artifact")
        return model

    def publish_view(self, definition: ViewDefinition) -> str:
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            model = conn.execute("SELECT recipe FROM models WHERE id=%s", (definition.model_id,)).fetchone()
            if not model:
                raise ValueError("Unknown model ID")
            recipe = ModelRecipe.model_validate(model["recipe"])
            cutoff = timestamp(definition.cutoff)
            if cutoff < timestamp(recipe.training_as_of):
                raise ValueError("Prediction cutoff precedes training information time")
            definition = definition.model_copy(update={"cutoff": str(cutoff)})
            name = recipe.task.name
            if conn.execute("SELECT count(*) n FROM views WHERE name<>%s", (name,)).fetchone()["n"] >= 128:
                raise Unavailable("View limit reached (128)")
            old = conn.execute("SELECT definition,definition_hash FROM views WHERE name=%s", (name,)).fetchone()
            if old and old["definition"] == definition.model_dump():
                return name
            # A new declaration epoch is distinct even when reverting to an old
            # identical payload. Repeated requests for the current payload remain
            # idempotent; A -> B -> A must enqueue new work instead of finding an
            # already completed A job at the same source revision.
            dh = digest([definition.model_dump(), str(uuid.uuid4())])
            conn.execute("INSERT INTO views(name,definition,definition_hash,task) VALUES(%s,%s,%s,%s) "
                         "ON CONFLICT(name) DO UPDATE SET definition=excluded.definition,definition_hash=excluded.definition_hash,"
                         "task=excluded.task,fence=views.fence+1,registered_at=clock_timestamp()",
                         (name, Jsonb(definition.model_dump()), dh, Jsonb(recipe.task.model_dump())))
            conn.execute("UPDATE jobs SET state='cancelled' WHERE view_name=%s AND state IN ('pending','running','failed')", (name,))
            self._update_progress(conn)
            self._enqueue(conn, meta["revision"])
        return name

    def read(self, name: str, *, generation: str | None = None, max_age_seconds: float | None = None,
             allow_stale: bool = False) -> Answer:
        if max_age_seconds is not None and (not 0 < max_age_seconds <= 86400):
            raise ValueError("Freshness bound must be in (0,86400]")
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            view = conn.execute("SELECT * FROM views WHERE name=%s", (name,)).fetchone()
            if not view:
                raise Unavailable("Unknown or purged view")
            selected = generation or view["generation"]
            row = conn.execute("SELECT * FROM generations WHERE id=%s AND view_name=%s", (selected, name)).fetchone()
            if not row:
                raise Unavailable("No retained published generation")
            raw = row["answer"]
            if row["revision"] == meta["revision"] and row["definition_hash"] == view["definition_hash"] and not meta["clock_unknown"]:
                # This is fresh *source evidence*: while holding the writer's
                # revision lock, the committed owned source is still exactly the
                # generation's immutable source state. Publication time is unchanged.
                raw["coverage"]["source_as_of"] = str(meta["now"])
            age = (meta["now"] - timestamp(raw["coverage"]["source_as_of"]).to_pydatetime()).total_seconds()
            budget = min(max_age_seconds, view["definition"]["freshness_seconds"]) if max_age_seconds is not None else view["definition"]["freshness_seconds"]
            reason = None
            state: Literal["fresh", "stale", "unknown"] = "fresh"
            if meta["clock_unknown"] or age < 0:
                state, reason, age = "unknown", "Clock discontinuity; operator acknowledgement and refresh required", None
            elif row["definition_hash"] != view["definition_hash"]:
                state, reason = "stale", "View definition/model/cutoff has changed"
            elif row["revision"] < meta["revision"]:
                state, reason = "stale", "Committed source changes are not covered"
            elif age > budget:
                state, reason = "stale", "Source observation exceeds freshness budget"
            if state != "fresh" and not allow_stale:
                raise Unavailable(reason)
            raw["coverage"] = Coverage(source_id=meta["source_id"], state=state, revision=row["revision"], current_revision=meta["revision"],
                                       source_as_of=raw["coverage"]["source_as_of"],
                                       observed_at=raw["coverage"]["observed_at"], age_seconds=age, reason=reason).model_dump()
            return Answer.model_validate(raw)

    def status(self) -> dict:
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            return {"api_version": "v1", "source_id": meta["source_id"], "revision": meta["revision"], "processed_revision": meta["processed_revision"],
                    "clock_unknown": meta["clock_unknown"], "purge_epoch": meta["purge_epoch"],
                    "views": conn.execute("SELECT name,definition,generation FROM views ORDER BY name").fetchall(),
                    "jobs": conn.execute("SELECT state,count(*) AS count FROM jobs GROUP BY state ORDER BY state").fetchall()}

    def cancel(self, name: str) -> None:
        with self.connect() as conn, conn.transaction():
            self._lock(conn)
            conn.execute("UPDATE views SET fence=fence+1 WHERE name=%s", (name,))
            conn.execute("UPDATE jobs SET state='cancelled' WHERE view_name=%s AND state IN ('pending','running','failed')", (name,))

    def retry(self, name: str) -> None:
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            # Retry the latest complete source boundary. Older failed work is
            # superseded only after this covering generation commits.
            conn.execute("UPDATE jobs j SET state='pending',attempts=0,error=NULL FROM views v "
                         "WHERE j.view_name=%s AND v.name=j.view_name AND j.definition_hash=v.definition_hash "
                         "AND j.revision=%s AND j.state IN ('failed','cancelled')", (name, meta['revision']))
            self._enqueue(conn, meta['revision'])

    def _require_canonical_identifiers(self, conn, schema: Schema, *, current_only: bool = False) -> None:
        scope = " AND valid_to IS NULL" if current_only else ""
        for table, columns in identifier_columns(schema).items():
            for name in dict.fromkeys(columns):
                if conn.execute("SELECT 1 FROM source_rows WHERE table_name=%s" + scope +
                                " AND jsonb_typeof(payload->%s) IS DISTINCT FROM 'string' LIMIT 1",
                                (table, name)).fetchone():
                    raise Conflict("Legacy identifiers require explicit offline migration; source preserved")
            if conn.execute("SELECT 1 FROM source_rows WHERE table_name=%s" + scope +
                            " AND payload->>%s IS DISTINCT FROM row_key LIMIT 1", (table, columns[0])).fetchone():
                raise Conflict("Legacy identifiers require explicit offline migration; source preserved")

    def purge_entity(self, entity_id: str, *, _ledger_state: tuple[str, str, str, int] | None = None) -> int:
        """Irreversible owner request: erase retained input and all derived artifacts.

        All models are invalidated because training can depend on every entity.
        Existing backups need the same purge ledger replayed by their owner.
        """
        if not isinstance(entity_id, str):
            raise ValueError("Purge requires the canonical string entity ID")
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            schema = Schema.model_validate(meta["schema_doc"])
            self._require_canonical_identifiers(conn, schema)
            if _ledger_state is not None:
                source_id, previous_hash, new_hash, count = _ledger_state
                if (meta["source_id"] != source_id or meta["ledger_hash"] != previous_hash
                        or count != meta["ledger_count"] + 1 or meta["unlogged_purge"]):
                    raise Conflict("Purge ledger does not extend this source's committed ledger")
                conn.execute("UPDATE meta SET ledger_hash=%s,ledger_count=%s", (new_hash, count))
            else:
                conn.execute("UPDATE meta SET unlogged_purge=true")
            keys = {(schema.entity_table, entity_id)}
            for e in schema.events:
                rows = conn.execute("SELECT DISTINCT row_key FROM source_rows WHERE table_name=%s AND payload->>%s=%s",
                                    (e.table, e.entity_key, entity_id)).fetchall()
                keys.update((e.table, r["row_key"]) for r in rows)
            for table, key in keys:
                conn.execute("DELETE FROM source_rows WHERE table_name=%s AND row_key=%s", (table, key))
            conn.execute("DELETE FROM views")
            conn.execute("DELETE FROM generations")
            conn.execute("DELETE FROM models")
            conn.execute("DELETE FROM snapshots")
            # Old change identities are sensitive as well. Keep only hash receipts.
            conn.execute("UPDATE outbox SET changes='[]'")
            revision = meta["revision"] + 1
            conn.execute("UPDATE meta SET revision=%s,purge_epoch=purge_epoch+1,processed_revision=%s", (revision, revision))
            conn.execute("INSERT INTO outbox(revision,request_id,request_hash,changes) VALUES(%s,%s,%s,'[]')",
                         (revision, 'purge-' + str(uuid.uuid4()), digest(entity_id)))
        return revision

    def retain(self, per_view: int = 5) -> dict:
        if per_view < 1:
            raise ValueError("Keep at least one generation")
        with self.connect() as conn, conn.transaction():
            meta = self._lock(conn)
            dropped = conn.execute("DELETE FROM generations WHERE (id,view_name) IN (SELECT id,view_name FROM "
                                   "(SELECT id,view_name,row_number() OVER(PARTITION BY view_name ORDER BY published_at DESC,id DESC) n "
                                   "FROM generations) ranked WHERE n>%s) AND NOT EXISTS "
                                   "(SELECT 1 FROM views v WHERE v.generation=generations.id AND v.name=generations.view_name) "
                                   "RETURNING id", (per_view,)).fetchall()
            conn.execute("DELETE FROM snapshots s WHERE NOT EXISTS(SELECT 1 FROM models m WHERE m.snapshot_id=s.id) "
                         "AND NOT EXISTS(SELECT 1 FROM generations g WHERE g.snapshot_id=s.id) "
                         "AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.snapshot_id=s.id AND j.state IN ('pending','running'))")
            floor = conn.execute("SELECT min(revision) AS r FROM jobs WHERE state IN ('pending','running','failed')").fetchone()["r"]
            floor = meta["revision"] if floor is None else floor
            conn.execute("DELETE FROM source_rows WHERE valid_to<=%s", (floor,))
            conn.execute("DELETE FROM jobs WHERE state IN ('done','superseded','cancelled') AND revision<%s", (floor,))
            return {"removed_generations": len(dropped), "source_history_floor": floor}

"""Private data-only backups and source-bound purge ledgers.

Restore is an offline cutover into a fresh schema. The operator must supply the
current ledger; this is not automatic deletion propagation to other live copies.
"""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from ..spec import digest
from .store import Conflict, Store

TABLES = ("meta", "source_rows", "outbox", "snapshots", "models", "views", "jobs", "generations", "costs")
JSON_COLUMNS = {"schema_doc", "payload", "changes", "recipe", "runtime", "definition", "task", "answer"}


def _sync_parent(path: Path) -> None:
    descriptor = os.open(path.resolve().parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _ledger(store: Store, path: Path):
    if path.is_symlink():
        raise ValueError("Purge ledger must be a regular owner file")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        if os.fstat(stream.fileno()).st_mode & 0o077:
            raise ValueError("Purge ledger must have owner-only permissions (0600)")
        text = stream.read()
        with store.connect() as conn:
            meta = conn.execute("SELECT source_id,ledger_hash,ledger_count,unlogged_purge FROM meta").fetchone()
        if not text:
            if meta["ledger_count"] or meta["unlogged_purge"]:
                raise Conflict("Cannot replace a missing committed purge ledger")
            header = {"format": "aheaddb-purge-ledger-v1", "source_id": meta["source_id"]}
            stream.write(json.dumps(header) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            _sync_parent(path)
            lines = [header]
        else:
            if not text.endswith("\n"):
                raise Conflict("Incomplete purge ledger record; owner repair required")
            lines = [json.loads(line) for line in text.splitlines()]
        header, records = lines[0], lines[1:]
        if header.get("format") != "aheaddb-purge-ledger-v1":
            raise Conflict("Invalid purge ledger header")
        for record in records:
            if (set(record) != {"entity_id", "request_id", "recorded_at"}
                    or not isinstance(record["entity_id"], str)):
                raise Conflict("Invalid purge ledger record")
        yield stream, header, records


def _synchronize(store: Store, header: dict, records: list[dict]) -> None:
    with store.connect() as conn:
        meta = conn.execute("SELECT * FROM meta").fetchone()
    count = meta["ledger_count"]
    if (meta["source_id"] != header["source_id"] or meta["unlogged_purge"]
            or len(records) < count or digest(records[:count]) != meta["ledger_hash"]):
        raise Conflict("Ledger does not match this source's committed purge history")
    # A recorded intent survives a source transaction failure. Replay every pending
    # intent before making a new backup or accepting another purge operation.
    for index in range(count, len(records)):
        store.purge_entity(records[index]["entity_id"], _ledger_state=(
            header["source_id"], digest(records[:index]), digest(records[:index + 1]), index + 1))


def purge(store: Store, entity_id: str, ledger: Path) -> int:
    with _ledger(store, ledger) as (stream, header, records):
        _synchronize(store, header, records)
        record = {"entity_id": entity_id, "request_id": str(uuid.uuid4()),
                  "recorded_at": datetime.now(timezone.utc).isoformat()}
        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        _sync_parent(ledger)
        _synchronize(store, header, [*records, record])
        return store.status()["revision"]


def backup(store: Store, destination: Path, ledger: Path) -> str:
    with _ledger(store, ledger) as (_, header, records):
        _synchronize(store, header, records)
        with store.connect() as conn, conn.transaction():
            meta = store._lock(conn)
            if (meta["unlogged_purge"] or meta["ledger_count"] != len(records)
                    or meta["ledger_hash"] != digest(records) or meta["source_id"] != header["source_id"]):
                raise Conflict("Source purge history changed before backup")
            size = conn.execute("SELECT sum(pg_total_relation_size(name::regclass)) n FROM unnest(%s::text[]) name",
                                (list(TABLES),)).fetchone()["n"]
            if size > 256 * 1024 * 1024:
                raise ValueError("Database exceeds portable backup limit; use native PostgreSQL tools")
            tables = {name: conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(name))).fetchall()
                      for name in TABLES}
            payload = json.loads(json.dumps({"format": "aheaddb-live-backup-v1", "tables": tables,
                                            "source_id": header["source_id"],
                                            "ledger_entries": len(records), "ledger_hash": digest(records)}, default=str))
        document = {"sha256": digest(payload), "payload": payload}
        data = json.dumps(document, allow_nan=False).encode()
        if len(data) > 256 * 1024 * 1024:
            raise ValueError("Backup exceeds 256 MiB portable limit; use native PostgreSQL tools")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_parent(destination)
        return document["sha256"]


def restore(store: Store, source: Path, ledger: Path) -> None:
    if source.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("Backup exceeds portable restore limit")
    document = json.loads(source.read_text())
    payload = document["payload"]
    if document["sha256"] != digest(payload) or payload["format"] != "aheaddb-live-backup-v1":
        raise Conflict("Backup checksum/format mismatch")
    tables = payload["tables"]
    if set(tables) != set(TABLES) or len(tables["meta"]) != 1 or tables["meta"][0]["version"] != 1:
        raise Conflict("Unsupported backup schema")
    if not ledger.is_file() or ledger.is_symlink() or ledger.stat().st_mode & 0o077:
        raise Conflict("Restore requires the existing current owner-only purge ledger")
    backup_meta = tables["meta"][0]
    if (backup_meta.get("unlogged_purge") or backup_meta.get("ledger_count") != payload["ledger_entries"]
            or backup_meta.get("ledger_hash") != payload["ledger_hash"]):
        raise Conflict("Backup lacks a bound committed purge history")
    store.migrate()
    # A fresh destination's source ID differs deliberately; do not initialize its
    # ledger. Lock and parse the source's existing ledger, held through import commit.
    descriptor = os.open(ledger, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        if os.fstat(stream.fileno()).st_mode & 0o077:
            raise Conflict("Purge ledger must have owner-only permissions")
        text = stream.read()
        if not text.endswith("\n"):
            raise Conflict("Incomplete purge ledger")
        lines = [json.loads(line) for line in text.splitlines()]
        header, records = lines[0], lines[1:]
        if (header.get("format") != "aheaddb-purge-ledger-v1" or header.get("source_id") != payload["source_id"]
                or tables["meta"][0]["source_id"] != payload["source_id"]):
            raise Conflict("Purge ledger belongs to a different source")
        prefix = records[:payload["ledger_entries"]]
        if len(records) < payload["ledger_entries"] or digest(prefix) != payload["ledger_hash"]:
            raise Conflict("Current ledger does not extend the backup's ledger")
        for record in records:
            if set(record) != {"entity_id", "request_id", "recorded_at"} or not isinstance(record["entity_id"], str):
                raise Conflict("Invalid purge ledger record")
        if records:
            entities = {r["entity_id"] for r in records}
            meta = tables["meta"][0]
            schema = meta["schema_doc"]
            removed = {(schema["entity_table"], row["row_key"]) for row in tables["source_rows"]
                       if row["table_name"] == schema["entity_table"] and row["row_key"] in entities}
            for event in schema["events"]:
                removed.update((row["table_name"], row["row_key"]) for row in tables["source_rows"]
                               if row["table_name"] == event["table"] and row["payload"].get(event["entity_key"]) in entities)
            tables["source_rows"] = [r for r in tables["source_rows"] if (r["table_name"], r["row_key"]) not in removed]
            for name in ("snapshots", "models", "views", "jobs", "generations", "costs"):
                tables[name] = []
            for receipt in tables["outbox"]:
                receipt["changes"] = []
            meta["revision"] += 1
            meta.update(processed_revision=meta["revision"], purge_epoch=len(records),
                        ledger_count=len(records), ledger_hash=digest(records), unlogged_purge=False)
            tables["outbox"].append({"revision": meta["revision"], "request_id": "restore-purge-" + str(uuid.uuid4()),
                                      "request_hash": digest(records), "changes": [],
                                      "authored_at": datetime.now(timezone.utc).isoformat()})
        with store.connect() as conn, conn.transaction():
            current = store._lock(conn)
            if current["schema_doc"] is not None or conn.execute("SELECT count(*) n FROM outbox").fetchone()["n"]:
                raise Conflict("Restore requires a fresh owned schema; existing data preserved")
            conn.execute("DELETE FROM meta")
            for name in TABLES:
                rows = tables[name]
                if not rows:
                    continue
                columns = list(rows[0])
                if any(set(row) != set(columns) for row in rows):
                    raise Conflict("Backup rows have inconsistent columns")
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(name), sql.SQL(',').join(map(sql.Identifier, columns)),
                    sql.SQL(',').join(sql.Placeholder() for _ in columns))
                values = [[Jsonb(row[c]) if c in JSON_COLUMNS and row[c] is not None else row[c] for c in columns] for row in rows]
                with conn.cursor() as cursor:
                    cursor.executemany(query, values)
            conn.execute("SELECT setval(pg_get_serial_sequence('jobs','id'),GREATEST(COALESCE((SELECT max(id) FROM jobs),1),1),"
                         "EXISTS(SELECT 1 FROM jobs))")
            conn.execute("UPDATE views SET fence=fence+1")
            conn.execute("UPDATE jobs SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'pending' END,lease_until=NULL "
                         "WHERE state='running'")
            conn.execute("UPDATE meta SET clock_unknown=true,last_clock=clock_timestamp()")

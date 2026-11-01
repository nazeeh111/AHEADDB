"""Real disk-exhaustion probe on an explicitly supplied disposable small volume.

Never fills the host volume. The mount must be distinct, writable and <=128 MiB.
Run with AHEAD_TEST_POSTGRES and AHEAD_TEST_DISK_MOUNT. All data is synthetic.
"""
from __future__ import annotations

import errno
import json
import os
import secrets
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

from aheaddb import Snapshot
from aheaddb.demo import demo_schema, demo_tables, demo_tasks
from aheaddb.live import Edit, ModelRecipe, Mutation, Store, ViewDefinition, Worker


def run():
    mount = Path(os.environ["AHEAD_TEST_DISK_MOUNT"]).resolve(strict=True)
    stats = os.statvfs(mount)
    capacity = stats.f_blocks * stats.f_frsize
    if not mount.is_mount() or not 0 < capacity <= 128 * 1024 * 1024:
        raise ValueError("Refusing to fill anything except an explicit <=128 MiB mounted test volume")
    namespace = "disk_probe_" + uuid.uuid4().hex[:10]
    tablespace = "disk_space_" + uuid.uuid4().hex[:10]
    directory = mount / tablespace
    directory.mkdir(mode=0o700)
    filler = mount / (namespace + ".filler")
    store = Store(os.environ["AHEAD_TEST_POSTGRES"], namespace)
    created = False
    try:
        store.migrate()
        store.bootstrap(Snapshot(demo_tables(entities=24), demo_schema()))
        for task in demo_tasks():
            model = store.register_model(ModelRecipe(task=task, training_cutoffs=("2024-03-01", "2024-04-01"),
                                                     training_as_of="2024-06-01", backend="persistence"))
            store.publish_view(ViewDefinition(model_id=model, cutoff="2024-07-15"))
        worker = Worker(store, "fifo", lease_seconds=300)
        while worker.once():
            pass
        before = store.status()["revision"]
        with store.connect() as conn:
            conn.execute(sql.SQL("CREATE TABLESPACE {} LOCATION {}").format(sql.Identifier(tablespace), sql.Literal(str(directory))))
            created = True
            conn.execute(sql.SQL("ALTER TABLE snapshots SET TABLESPACE {}").format(sql.Identifier(tablespace)))
        written = 0
        with filler.open("wb", buffering=0) as stream:
            for chunk_size in (1024 * 1024, 65536, 4096, 512):
                for _ in range(capacity // chunk_size + 1):
                    try:
                        written += stream.write(b'X' * chunk_size)
                    except OSError as exc:
                        if exc.errno != errno.ENOSPC:
                            raise
                        break
                    if written > capacity:
                        raise AssertionError("Bounded test volume unexpectedly exceeded capacity")
        request = Edit(request_id="actual-enospc", mutations=(Mutation(table="orders", key="disk-row", row={
            "id": "disk-row", "entity_id": "C0000", "occurred_at": "2024-07-14T12:00:00Z",
            "available_at": "2024-07-14T13:00:00Z", "amount": 998.1234567890123, "test_entropy": secrets.token_hex(65536)}),))
        exhausted = False
        try:
            store.edit(request)
        except psycopg.errors.DiskFull:
            exhausted = True
        finally:
            filler.unlink(missing_ok=True)
        assert exhausted, "Public edit did not encounter actual PostgreSQL DiskFull"
        assert store.status()["revision"] == before
        assert "disk-row" not in set(store.snapshot()[1].table("orders").id)
        committed = store.edit(request)
        while worker.once():
            pass
        for task in demo_tasks():
            answer = store.read(task.name)
            assert answer.coverage.revision == committed
            assert answer.coverage.state == "fresh"
        return {"actual_enospc": True, "volume_capacity_bytes": capacity,
                "filler_bytes": written, "rollback_revision": before, "continued_revision": committed,
                "continued_features": [t.name for t in demo_tasks()]}
    finally:
        filler.unlink(missing_ok=True)
        with store.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(namespace)))
            if created:
                conn.execute(sql.SQL("DROP TABLESPACE {}").format(sql.Identifier(tablespace)))
        directory.rmdir()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

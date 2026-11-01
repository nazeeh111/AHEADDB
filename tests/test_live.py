"""Real PostgreSQL tests. No SQLite/mocked durability substitute."""
# ruff: noqa: E402  # Optional-extra collection guard must precede Live imports.
from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from live_reference import reference_features

pytest.importorskip("psycopg")
from psycopg import sql

from aheaddb import Snapshot
from aheaddb.demo import demo_schema, demo_tables, demo_tasks
from aheaddb.live import Answer, Edit, ModelRecipe, Mutation, Store, Unavailable, ViewDefinition, Worker
from aheaddb.live.api import create_live_app
from aheaddb.live.store import Conflict, decode_snapshot, encode_snapshot


@pytest.fixture
def store():
    dsn = os.environ.get("AHEAD_TEST_POSTGRES")
    if not dsn:
        pytest.skip("Set AHEAD_TEST_POSTGRES to an owned disposable database")
    store = Store(dsn, "test_live_" + uuid.uuid4().hex[:12])
    store.migrate()
    yield store
    with store.connect() as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(store.namespace)))


@pytest.fixture
def live(store):
    snapshot = Snapshot(demo_tables(entities=24), demo_schema())
    store.bootstrap(snapshot)
    for task in demo_tasks():
        mid = store.register_model(ModelRecipe(task=task, training_cutoffs=("2024-03-01", "2024-04-01"),
                                               training_as_of="2024-06-01", backend="persistence"))
        store.publish_view(ViewDefinition(model_id=mid, cutoff="2024-07-15"))
    worker = Worker(store, "fifo")
    while worker.once():
        pass
    return store


def edit(key="new", owner="C0000", amount=450, request=None):
    return Edit(request_id=request or str(uuid.uuid4()), mutations=(Mutation(table="orders", key=key, row={
        "id": key, "entity_id": owner, "occurred_at": "2024-07-14T00:00:00Z",
        "available_at": "2024-07-14T01:00:00Z", "amount": amount}),))


def drain(store, policy="fifo"):
    worker = Worker(store, policy)
    for _ in range(100):
        result = worker.once()
        if not result:
            status = store.status()
            if not any(j["state"] in {"pending", "running"} for j in status["jobs"]):
                return
            time.sleep(0.06)
    raise AssertionError("Did not drain")


def agreement(store):
    import psycopg
    app = create_live_app(store)
    database_errors = []
    original_handler = app.exception_handlers[psycopg.Error]
    async def diagnose(request, exc):
        # Task-local synthetic test evidence only. Public errors remain generic.
        database_errors.append(f"{type(exc).__name__}: {exc}")
        return await original_handler(request, exc)
    app.add_exception_handler(psycopg.Error, diagnose)
    for task in demo_tasks():
        response = TestClient(app).get(f"/v1/views/{task.name}")
        assert response.status_code == 200, (response.text, database_errors)
        answer = Answer.model_validate(response.json())
        with store.connect() as conn:
            payload = conn.execute("SELECT payload FROM snapshots WHERE id=%s", (answer.snapshot_id,)).fetchone()["payload"]
        snapshot = decode_snapshot(payload)
        model = store.load_model(answer.model_id)
        features = reference_features(snapshot, task, answer.prediction_cutoff)
        expected = model.predict(features[model.columns])
        assert set(answer.predictions) == set(features.index)
        np.testing.assert_allclose([answer.predictions[i] for i in features.index], expected, rtol=1e-12, atol=1e-10)


def test_roundtrip_bootstrap_durable_two_features(live):
    assert live.status()["revision"] == live.status()["processed_revision"] == 1
    agreement(live)
    new_process = Store(live._dsn, live.namespace)
    assert new_process.read("return_14d").generation == live.read("return_14d").generation
    with pytest.raises(Conflict):
        live.bootstrap(Snapshot(demo_tables(entities=24), demo_schema()))
    assert decode_snapshot(encode_snapshot(live.snapshot()[1])).version == live.snapshot()[1].version


def test_edits_retain_cutoff_owner_move_delete_and_schema(live):
    original = live.read("revenue_14d")
    live.edit(edit())
    with pytest.raises(Unavailable, match="Committed"):
        live.read("revenue_14d")
    assert live.read("revenue_14d", allow_stale=True).coverage.state == "stale"
    drain(live)
    assert live.read("revenue_14d").predictions["C0000"] != original.predictions["C0000"]
    agreement(live)
    live.edit(edit(owner="C0001"))
    drain(live)
    agreement(live)
    live.edit(Edit(request_id=str(uuid.uuid4()), mutations=(Mutation(table="orders", key="new"),)))
    drain(live)
    agreement(live)
    old = live.read("revenue_14d", generation=original.generation, allow_stale=True)
    assert old.coverage.state == "stale"
    with pytest.raises(Unavailable):
        live.read("revenue_14d", generation=original.generation)
    rev = live.status()["revision"]
    with pytest.raises(ValueError):
        live.edit(edit(owner="orphan"))
    assert live.status()["revision"] == rev
    with pytest.raises(ValueError):
        live.edit(Edit(request_id="unknown", mutations=(Mutation(table="no_sql;DROP", key="x"),)))


def test_request_dedup_and_atomic_rollback(live):
    request = edit(request="committed-once")
    revision = live.edit(request)
    assert live.edit(request) == revision
    with pytest.raises(Conflict):
        live.edit(edit(amount=2, request="committed-once"))
    def fail(boundary):
        if boundary == "edit_before_commit":
            raise RuntimeError("simulated")
    with pytest.raises(RuntimeError):
        live.edit(edit(key="rolled-back"), fault=fail)
    assert live.status()["revision"] == revision
    assert "rolled-back" not in set(live.snapshot()[1].table("orders").id)


def test_allocated_ids_not_commit_order_and_bootstrap_stream_race(live):
    # Request IDs allocated in order A,B. B commits first; cursor follows locked
    # source revisions, never lexicographic/allocation order of request IDs.
    a = edit(key="A", request="allocated-first")
    b = edit(key="B", request="allocated-second")
    assert live.edit(b) < live.edit(a)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(live.edit, edit(key="concurrent")), pool.submit(live.snapshot)]
        results = [f.result() for f in futures]
    sid, snap = results[1]
    assert snap.version
    drain(live, "budget")
    agreement(live)
    assert live.status()["processed_revision"] == live.status()["revision"]


@pytest.mark.parametrize("boundary", ["claim_before_commit", "claim_after_commit", "compute_before", "compute_after",
                                      "publish_before_transaction", "publish_before_commit", "publish_after_commit"])
def test_fault_boundaries_recover_contents_and_continue(live, boundary):
    live.edit(edit())
    def crash(point):
        if point == boundary:
            raise SystemExit(71)  # bypass ordinary failure handling, like process loss
    worker = Worker(live, "fifo", lease_seconds=300, fault=crash)
    with pytest.raises(SystemExit):
        worker.once()
    with live.connect() as conn:
        conn.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE state='running'")
    drain(Store(live._dsn, live.namespace))
    agreement(live)
    live.edit(edit(key="after-recovery", owner="C0002"))
    drain(live)
    agreement(live)


def test_expired_worker_fenced_and_cancelled(live):
    live.edit(edit())
    slow = Worker(live, "fifo", lease_seconds=0.03)
    claim = slow.claim()
    answers, receipt = slow.compute(claim)
    time.sleep(0.05)
    drain(live)
    with pytest.raises(Conflict, match="fenced"):
        slow.publish(claim, answers, receipt, 1)
    live.edit(edit(key="cancelled"))
    worker = Worker(live, "fifo")
    claim = worker.claim()
    answers, receipt = worker.compute(claim)
    live.cancel(claim.jobs[0]["view_name"])
    with pytest.raises(Conflict):
        worker.publish(claim, answers, receipt, 0.1)


def test_poison_attempts_backpressure(live):
    live.edit(edit())
    worker = Worker(live, "fifo", max_attempts=2)
    worker.compute = lambda claim: (_ for _ in ()).throw(ValueError("private rows not stored"))
    for _ in range(2):
        with pytest.raises(ValueError):
            worker.once()
    assert any(j["state"] == "failed" for j in live.status()["jobs"])
    live.max_backlog = 0
    before = live.status()["revision"]
    with pytest.raises(Unavailable, match="Backlog"):
        live.edit(edit(key="rejected"))
    assert live.status()["revision"] == before
    live.retry("return_14d")
    live.retry("revenue_14d")
    drain(live)
    agreement(live)


def test_definition_change_and_purge_retention(live):
    old = live.read("return_14d")
    with live.connect() as conn:
        definition = conn.execute("SELECT definition FROM views WHERE name='return_14d'").fetchone()["definition"]
    live.publish_view(ViewDefinition(**{**definition, "cutoff": "2024-07-16"}))
    with pytest.raises(Unavailable, match="definition"):
        live.read("return_14d")
    drain(live)
    agreement(live)
    live.retain(1)
    with pytest.raises(Unavailable, match="retained"):
        live.read("return_14d", generation=old.generation, allow_stale=True)
    live.purge_entity("C0000")
    with pytest.raises(Unavailable):
        live.read("return_14d", generation=old.generation, allow_stale=True)
    with live.connect() as conn:
        assert conn.execute("SELECT count(*) n FROM models").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) n FROM source_rows WHERE payload->>'entity_id'='C0000'").fetchone()["n"] == 0


def test_http_version_host_origin_and_contract(live):
    client = TestClient(create_live_app(live))
    response = client.get("/v1/views/return_14d")
    assert response.status_code == 200
    assert response.json()["api_version"] == "v1"
    assert client.get("/v1/status", headers={"Host": "evil.invalid"}).status_code == 400
    assert client.post("/v1/source/edits", headers={"Origin": "https://evil.invalid"}, json=edit().model_dump()).status_code == 403
    assert client.post("/v1/source/edits", json=edit().model_dump()).status_code == 200
    assert client.get("/v1/views/return_14d").status_code == 503
    assert client.get("/v1/views/return_14d?allow_stale=true").json()["coverage"]["state"] == "stale"


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.tuples(st.integers(0, 5), st.integers(0, 3), st.integers(-20, 1000)), min_size=1, max_size=10))
def test_generated_operation_sequences(live, operations):
    for index, owner, amount in operations:
        live.edit(edit(key=f"generated-{index}", owner=f"C{owner:04d}", amount=amount))
    drain(live, "budget")
    agreement(live)


def test_clock_jump_persists_after_rejected_read(live):
    with live.connect() as conn:
        conn.execute("UPDATE meta SET last_clock=clock_timestamp()+interval '60 seconds'")
    with pytest.raises(Unavailable, match="Clock"):
        live.read("return_14d")
    assert Store(live._dsn, live.namespace).status()["clock_unknown"]
    assert live.read("return_14d", allow_stale=True).coverage.state == "unknown"
    live.acknowledge_clock()
    drain(live)
    agreement(live)


def test_registration_backlog_limit_is_transactional(live):
    live.max_backlog = 1
    before = live.status()["revision"]
    with pytest.raises(Unavailable, match="Backlog"):
        live.edit(edit(key="would-add-two-jobs"))
    assert live.status()["revision"] == before
    assert live.read("return_14d").coverage.state == "fresh"


def test_commit_lock_serializes_transactions(live):
    import threading
    locked, release = threading.Event(), threading.Event()
    # B's ID is allocated first but transaction A obtains the revision lock first.
    b = edit(key="allocated-before-lock")
    a = edit(key="locked-first")
    def hold(point):
        if point == "edit_before_commit":
            locked.set()
            assert release.wait(30)
    with ThreadPoolExecutor(2) as pool:
        fa = pool.submit(live.edit, a, hold)
        assert locked.wait(30)
        fb = pool.submit(live.edit, b)
        time.sleep(0.1)
        assert not fb.done()
        release.set()
        assert fa.result() + 1 == fb.result()
    drain(live)
    agreement(live)


@pytest.mark.parametrize("boundary", ["claim_before_commit", "claim_after_commit", "compute_before", "compute_after",
                                      "publish_before_transaction", "publish_before_commit", "publish_after_commit"])
def test_abrupt_process_loss_at_durability_boundaries(live, boundary):
    import subprocess
    import sys
    live.edit(edit())
    script = '''
import os
from aheaddb.live import Store, Worker
store=Store(os.environ['AHEAD_TEST_POSTGRES'],os.environ['AHEAD_TEST_NAMESPACE'])
def die(point):
    if point==os.environ['AHEAD_TEST_BOUNDARY']: os._exit(71)
Worker(store,'fifo',lease_seconds=300,fault=die).once()
raise SystemExit(72)
'''
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": "937",
                            "AHEAD_TEST_NAMESPACE": live.namespace, "AHEAD_TEST_BOUNDARY": boundary},
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 71, result.stderr
    with live.connect() as conn:
        conn.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE state='running'")
    drain(Store(live._dsn, live.namespace))
    agreement(live)
    live.edit(edit(key="continued-after-process-loss", owner="C0003"))
    drain(live)
    agreement(live)


def test_failed_migration_preserves_existing_database(live, monkeypatch):
    import aheaddb.live.store as module
    before = live.read("return_14d").generation
    monkeypatch.setattr(module, "DDL", module.DDL + "CREATE TABLE migration_partial(n int); SELECT 1/0;")
    import psycopg
    with pytest.raises(psycopg.errors.DivisionByZero):
        live.migrate()
    with live.connect() as conn:
        assert conn.execute("SELECT to_regclass('migration_partial') n").fetchone()["n"] is None
    assert live.read("return_14d").generation == before


def test_injected_postgresql_disk_full_rolls_back_publication_and_edit(live):
    import psycopg
    live.edit(edit())
    original = live.read("return_14d", allow_stale=True).generation
    with live.connect() as conn:
        conn.execute("CREATE FUNCTION inject_disk_full() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                     "RAISE EXCEPTION 'injected storage exhaustion' USING ERRCODE='53100'; END $$")
        conn.execute("CREATE TRIGGER disk_full BEFORE INSERT ON generations FOR EACH STATEMENT EXECUTE FUNCTION inject_disk_full()")
    with pytest.raises(psycopg.errors.DiskFull):
        Worker(live, "fifo").once()
    assert live.read("return_14d", allow_stale=True).generation == original
    with live.connect() as conn:
        conn.execute("DROP TRIGGER disk_full ON generations")
        conn.execute("CREATE TRIGGER disk_full BEFORE INSERT ON snapshots FOR EACH STATEMENT EXECUTE FUNCTION inject_disk_full()")
    revision = live.status()["revision"]
    with pytest.raises(psycopg.errors.DiskFull):
        live.edit(edit(key="no-space-to-snapshot"))
    assert live.status()["revision"] == revision
    with live.connect() as conn:
        conn.execute("DROP TRIGGER disk_full ON snapshots")
    drain(live)
    agreement(live)


def test_http_never_echoes_invalid_private_rows_and_bounds_chunked_body(live):
    client = TestClient(create_live_app(live))
    private = "PRIVATE-ROW-MARKER"
    response = client.post("/v1/source/edits", json={"request_id": private, "mutations": "invalid"})
    assert response.status_code == 422
    assert private not in response.text
    response = client.post("/v1/source/edits", content=iter([b'x' * 600000, b'x' * 600000]),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_backup_restore_recovery_and_purge_ledger(live, tmp_path):
    from aheaddb.live.maintenance import backup, purge, restore
    ledger = tmp_path / "purges.jsonl"
    ledger.touch(mode=0o600)
    saved = tmp_path / "source.json"
    backup(live, saved, ledger)
    target = Store(live._dsn, "test_restore_" + uuid.uuid4().hex[:10])
    try:
        restore(target, saved, ledger)
        assert target.status()["clock_unknown"]
        target.acknowledge_clock()
        drain(target)
        agreement(target)
        target.edit(edit(key="after-restore"))
        drain(target)
        agreement(target)
        purge(live, "C0000", ledger)
        purged = Store(live._dsn, "test_restore_purge_" + uuid.uuid4().hex[:10])
        try:
            restore(purged, saved, ledger)
            with purged.connect() as conn:
                assert conn.execute("SELECT count(*) n FROM source_rows WHERE payload->>'entity_id'='C0000'").fetchone()["n"] == 0
                assert conn.execute("SELECT count(*) n FROM models").fetchone()["n"] == 0
            with pytest.raises(Unavailable):
                purged.read("return_14d", allow_stale=True)
        finally:
            with purged.connect() as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(purged.namespace)))
        with pytest.raises(Conflict, match="fresh"):
            restore(target, saved, ledger)
    finally:
        with target.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(target.namespace)))


def test_purge_ledger_source_binding_and_pending_intent_recovery(store, tmp_path, monkeypatch):
    from aheaddb.live.maintenance import backup, purge, restore
    store.bootstrap(Snapshot(demo_tables(entities=2), demo_schema()))
    ledger = tmp_path / "ledger.jsonl"
    old = tmp_path / "before.json"
    backup(store, old, ledger)
    original = store.purge_entity
    def unavailable(*args, **kwargs):
        raise RuntimeError("source failure after durable deletion intent")
    monkeypatch.setattr(store, "purge_entity", unavailable)
    with pytest.raises(RuntimeError):
        purge(store, "C0000", ledger)
    assert store.status()["revision"] == 1
    monkeypatch.setattr(store, "purge_entity", original)
    backup(store, tmp_path / "after.json", ledger)  # pending intent must be replayed
    assert "C0000" not in set(store.snapshot()[1].table("entities").entity_id)
    with store.connect() as conn:
        meta = conn.execute("SELECT ledger_count,purge_epoch,unlogged_purge FROM meta").fetchone()
    assert meta == {"ledger_count": 1, "purge_epoch": 1, "unlogged_purge": False}
    wrong = tmp_path / "wrong.jsonl"
    wrong.write_text('{"format":"aheaddb-purge-ledger-v1","source_id":"unrelated"}\n')
    wrong.chmod(0o600)
    target = Store(store._dsn, "test_ledger_" + uuid.uuid4().hex[:10])
    try:
        with pytest.raises(Conflict, match="different source"):
            restore(target, old, wrong)
        restore(target, old, ledger)
        assert "C0000" not in set(target.snapshot()[1].table("entities").entity_id)
    finally:
        with target.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(target.namespace)))


def test_unlogged_direct_purge_refuses_portable_backup(store, tmp_path):
    from aheaddb.live.maintenance import backup
    store.bootstrap(Snapshot(demo_tables(entities=2), demo_schema()))
    store.purge_entity("C0000")
    with pytest.raises(Conflict, match="ledger"):
        backup(store, tmp_path / "unsafe.json", tmp_path / "missing.jsonl")


def test_http_generic_bootstrap_evaluate_publish_flow(store):
    from aheaddb.live.contracts import EvaluationReport
    client = TestClient(create_live_app(store))
    source = Snapshot(demo_tables(entities=24), demo_schema())
    bootstrap = client.post('/v1/source/bootstrap', json=encode_snapshot(source))
    assert bootstrap.status_code == 200, bootstrap.text
    assert client.post('/v1/source/bootstrap', json=encode_snapshot(source)).status_code == 409
    task = demo_tasks()[0]
    recipe = ModelRecipe(task=task, training_cutoffs=('2024-03-01', '2024-04-01'),
                         training_as_of='2024-06-01', backend='persistence')
    evaluation = client.post('/v1/evaluations', json={
        'recipe': recipe.model_dump(), 'backtest_cutoffs': ['2024-03-01','2024-04-01','2024-05-01','2024-06-01'],
        'labels_as_of': '2024-08-25', 'prediction_cutoff': '2024-07-15'})
    assert evaluation.status_code == 200, evaluation.text
    report = EvaluationReport.model_validate(evaluation.json())
    assert report.revision == 1 and report.data_version == source.version
    assert {f['backend'] for f in report.report['folds']} == {'baseline','histogram','persistence'}
    assert report.plan['task']['horizon_days'] == 14
    model = client.post('/v1/models', json=recipe.model_dump()).json()['model_id']
    assert client.post('/v1/views', json={'model_id': model, 'cutoff': '2024-07-15'}).status_code == 200
    drain(store)
    answer = client.get('/v1/views/return_14d').json()
    assert answer['coverage']['source_id'] == report.source_id
    assert answer['publication_time_semantics'] == 'transaction_clock_lower_bound'
    schema = client.get('/openapi.json').json()
    assert schema['paths']['/v1/status']['get']['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/Status')
    assert schema['paths']['/v1/views/{name}']['get']['responses']['422']['content']['application/json']['schema']['$ref'].endswith('/ErrorResponse')


def test_failed_jobs_count_toward_backlog_and_replacement_recovers(live):
    live.max_backlog = 2
    live.edit(edit(key='poison-bound'))
    worker = Worker(live, 'fifo', max_attempts=1)
    worker.compute = lambda claim: (_ for _ in ()).throw(ValueError('simulated poison'))
    with pytest.raises(ValueError):
        worker.once()
    assert any(j['state'] == 'failed' and j['count'] == 2 for j in live.status()['jobs'])
    revision = live.status()['revision']
    with pytest.raises(Unavailable, match='Backlog'):
        live.edit(edit(key='must-wait-for-recovery'))
    assert live.status()['revision'] == revision
    recipe = ModelRecipe(task=demo_tasks()[0], training_cutoffs=('2024-03-01','2024-04-01'),
                         training_as_of='2024-06-01', backend='baseline')
    model = live.register_model(recipe)
    live.publish_view(ViewDefinition(model_id=model, cutoff='2024-07-15'))
    live.retry('revenue_14d')
    drain(live)
    agreement(live)


def test_compressible_source_cannot_bypass_decoded_byte_limit(live):
    with live.connect() as conn:
        size = conn.execute("SELECT sum(octet_length(payload::text)) n FROM source_rows WHERE valid_to IS NULL").fetchone()['n']
    live.max_source_bytes = int(size) + 1000
    request = edit(key='compressed-size-probe').model_dump()
    request['mutations'][0]['row']['large_text'] = 'A' * 100000
    revision = live.status()['revision']
    with pytest.raises(Unavailable, match='row/byte limit'):
        live.edit(Edit.model_validate(request))
    assert live.status()['revision'] == revision


def test_idle_worker_releases_managed_caches_after_purge(live):
    live.edit(edit(key='cache-before-purge'))
    worker = Worker(live, 'fifo')
    assert worker.once()
    assert worker._engines and worker._models
    live.purge_entity('C0000')
    assert worker.once() is None
    assert not worker._engines and not worker._models


@pytest.mark.parametrize('boundary', ['edit_before_commit', 'edit_after_commit'])
def test_abrupt_source_transaction_loss_is_deduplicated(live, boundary):
    import subprocess
    import sys
    request = edit(key='source-process-loss', request='source-durable-request')
    script = '''
import os
from aheaddb.live import Store,Edit
store=Store(os.environ['AHEAD_TEST_POSTGRES'],os.environ['AHEAD_TEST_NAMESPACE'])
def die(point):
    if point==os.environ['AHEAD_TEST_BOUNDARY']: os._exit(71)
store.edit(Edit.model_validate_json(os.environ['AHEAD_TEST_EDIT']),fault=die)
raise SystemExit(72)
'''
    before = live.status()['revision']
    result = subprocess.run([sys.executable, '-c', script], env={**os.environ,
        'AHEAD_TEST_NAMESPACE': live.namespace, 'AHEAD_TEST_BOUNDARY': boundary,
        'AHEAD_TEST_EDIT': request.model_dump_json()}, capture_output=True, text=True, timeout=180)
    assert result.returncode == 71, result.stderr
    assert live.status()['revision'] == before + int(boundary == 'edit_after_commit')
    committed = live.edit(request)
    assert committed == before + 1
    assert live.edit(request) == committed
    drain(live)
    agreement(live)


def test_registration_round_trip_epoch_and_progress_are_truthful(live):
    original = live.read('return_14d')
    a = ViewDefinition(model_id=original.model_id, cutoff=original.prediction_cutoff)
    b_model = live.register_model(ModelRecipe(task=demo_tasks()[0], training_cutoffs=('2024-03-01','2024-04-01'),
                                              training_as_of='2024-06-01', backend='baseline'))
    b = ViewDefinition(model_id=b_model, cutoff=original.prediction_cutoff)
    live.publish_view(b)
    assert live.status()['processed_revision'] == 0
    drain(live)
    assert live.read('return_14d').model_id == b_model
    live.publish_view(a)
    live.publish_view(a)  # retry of current declaration must not create extra jobs
    assert live.status()['processed_revision'] == 0
    live.retry('return_14d')
    drain(live)
    current = live.read('return_14d')
    assert current.model_id == original.model_id and current.generation != original.generation
    assert live.status()['processed_revision'] == live.status()['revision']
    agreement(live)
    live.acknowledge_clock()
    assert live.status()['processed_revision'] == 0
    drain(live)
    agreement(live)


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.tuples(st.integers(0, 3), st.integers(0, 3), st.integers(-20, 1000), st.booleans()),
                min_size=3, max_size=9))
def test_generated_concurrent_writers_and_coherent_snapshots(live, operations):
    import threading
    prefix = 'parallel-' + uuid.uuid4().hex + '-'
    before = live.status()['revision']
    barrier = threading.Barrier(4)

    def writer(lane):
        barrier.wait(timeout=30)
        receipts = []
        for index in range(lane, len(operations), 3):
            key, owner, amount, delete = operations[index]
            row_id = prefix + str(key)
            transaction = (Edit(request_id=str(uuid.uuid4()), mutations=(
                Mutation(table='orders', key=row_id, row=None),)) if delete else
                edit(key=row_id, owner=f'C{owner:04d}', amount=amount))
            revision = live.edit(transaction)
            receipts.append((revision, row_id, None if delete else (f'C{owner:04d}', float(amount))))
        return receipts

    def reader():
        barrier.wait(timeout=30)
        return [live.snapshot() for _ in range(3)]

    with ThreadPoolExecutor(4) as pool:
        writers = [pool.submit(writer, lane) for lane in range(3)]
        reads = pool.submit(reader)
        receipts = sorted(row for future in writers for row in future.result())
        captured = reads.result()
    captured.append(live.snapshot())
    assert [r[0] for r in receipts] == list(range(before + 1, before + len(operations) + 1))
    current, expected = {}, {before: {}}
    for revision, key, value in receipts:
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
        expected[revision] = current.copy()
    for snapshot_id, snapshot in captured:
        with live.connect() as conn:
            revision = conn.execute('SELECT revision FROM snapshots WHERE id=%s', (snapshot_id,)).fetchone()['revision']
        rows = snapshot.table('orders')
        actual = {r.id: (r.entity_id, float(r.amount)) for r in rows.itertuples() if r.id.startswith(prefix)}
        assert actual == expected[revision]
    drain(live, 'budget')
    agreement(live)
    assert live.status()['processed_revision'] == before + len(operations)


@pytest.mark.parametrize('owner', [1, True, 1.0], ids=['integer', 'boolean', 'float'])
@pytest.mark.parametrize('legacy_payload', [False, True], ids=['new-edit', 'legacy-edit'])
def test_purge_and_restore_cover_scalar_identifiers(store, tmp_path, owner, legacy_payload):
    import json

    from psycopg.types.json import Jsonb

    from aheaddb.live.maintenance import backup, purge, restore
    from aheaddb.spec import digest
    owner_id = str(owner)
    tables = demo_tables(entities=2)
    mapping = {'C0000': owner_id, 'C0001': 'other-owner'}
    tables['entities']['entity_id'] = tables['entities']['entity_id'].replace(mapping)
    tables['orders']['entity_id'] = tables['orders']['entity_id'].replace(mapping)
    store.bootstrap(Snapshot(tables, demo_schema()))
    store.edit(edit(key='scalar-owner-event', owner=owner))
    with store.connect() as conn:
        payload = conn.execute("SELECT payload FROM source_rows WHERE table_name='orders' "
                               "AND row_key='scalar-owner-event'").fetchone()['payload']
        assert payload['entity_id'] == owner_id and isinstance(payload['entity_id'], str)
    ledger, saved = tmp_path / 'purges.jsonl', tmp_path / 'before.json'
    backup(store, saved, ledger)
    target = Store(store._dsn, 'test_scalar_restore_' + uuid.uuid4().hex[:10])
    try:
        if legacy_payload:
            # Simulate a pre-release accepted database and backup. Ownership of
            # numeric columns can be ambiguous, so reject rather than guess.
            with store.connect() as conn:
                conn.execute("UPDATE source_rows SET payload=jsonb_set(payload,'{entity_id}',%s) "
                             "WHERE table_name='orders' AND row_key='scalar-owner-event'", (Jsonb(owner),))
            document = json.loads(saved.read_text())
            for row in document['payload']['tables']['source_rows']:
                if row['row_key'] == 'scalar-owner-event':
                    row['payload']['entity_id'] = owner
            document['sha256'] = digest(document['payload'])
            saved.write_text(json.dumps(document))
            before = store.status()['revision']
            intent_before = ledger.read_bytes()
            with pytest.raises(Conflict, match='Legacy identifiers'):
                purge(store, owner_id, ledger)
            assert ledger.read_bytes() == intent_before
            with pytest.raises(Conflict, match='Legacy identifiers'):
                store.purge_entity(owner_id)
            with pytest.raises(Conflict, match='Legacy identifiers'):
                store.edit(edit(key='must-not-reinterpret-owners'))
            with pytest.raises(Conflict, match='Legacy identifiers'):
                backup(store, tmp_path / 'rejected.json', ledger)
            assert not (tmp_path / 'rejected.json').exists()
            assert store.status()['revision'] == before
            with pytest.raises(Conflict, match='Legacy identifiers'):
                restore(target, saved, ledger)
            with target.connect() as conn:
                assert conn.execute('SELECT to_regnamespace(%s) n', (target.namespace,)).fetchone()['n'] is None
            with store.connect() as conn:
                assert conn.execute("SELECT count(*) n FROM source_rows WHERE row_key='scalar-owner-event'").fetchone()['n'] == 1
            return
        purge(store, owner_id, ledger)
        with store.connect() as conn:
            assert conn.execute("SELECT count(*) n FROM source_rows WHERE row_key='scalar-owner-event'").fetchone()['n'] == 0
        assert set(store.snapshot()[1].table('entities').entity_id) == {'other-owner'}
        restore(target, saved, ledger)
        assert set(target.snapshot()[1].table('entities').entity_id) == {'other-owner'}
        with target.connect() as conn:
            assert conn.execute("SELECT count(*) n FROM source_rows WHERE row_key='scalar-owner-event'").fetchone()['n'] == 0
    finally:
        with target.connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target.namespace)))


@pytest.mark.parametrize('owner', [None, {}, [], float('inf'), float('nan')],
                         ids=['null', 'object', 'array', 'infinity', 'nan'])
def test_invalid_live_identifier_rolls_back(store, owner):
    store.bootstrap(Snapshot(demo_tables(entities=2), demo_schema()))
    with pytest.raises(ValueError, match='Identifiers'):
        store.edit(edit(key='invalid-owner', owner=owner))
    assert store.status()['revision'] == 1
    assert 'invalid-owner' not in set(store.snapshot()[1].table('orders').id)


def test_legacy_identifier_in_retained_history_blocks_purge(store, tmp_path):
    from psycopg.types.json import Jsonb

    from aheaddb.live.maintenance import backup, purge
    store.bootstrap(Snapshot(demo_tables(entities=2), demo_schema()))
    store.edit(edit(key='moved-legacy-event'))
    store.edit(edit(key='moved-legacy-event', owner='C0001'))
    with store.connect() as conn:
        conn.execute("UPDATE source_rows SET payload=jsonb_set(payload,'{entity_id}',%s) "
                     "WHERE row_key='moved-legacy-event' AND valid_to IS NOT NULL", (Jsonb(True),))
    before = store.status()['revision']
    assert store.snapshot()[1].table('orders').set_index('id').loc['moved-legacy-event','entity_id'] == 'C0001'
    with pytest.raises(Conflict, match='Legacy identifiers'):
        purge(store, 'C0000', tmp_path / 'ledger.jsonl')
    with pytest.raises(Conflict, match='Legacy identifiers'):
        backup(store, tmp_path / 'rejected.json', tmp_path / 'ledger.jsonl')
    assert store.status()['revision'] == before
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) n FROM source_rows WHERE row_key='moved-legacy-event'").fetchone()['n'] == 2

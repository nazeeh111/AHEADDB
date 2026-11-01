"""Unclean PostgreSQL recovery probe, restricted to a marked disposable cluster.

Back up actual database bytes through pg_dump, verify restore in a separate DB,
then stop immediately, observe unavailable HTTP reads, recover WAL and continue.
Never use a real/production database. Caller supplies installed free PG binaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from aheaddb import Snapshot
from aheaddb.demo import demo_schema, demo_tables, demo_tasks
from aheaddb.live import Edit, ModelRecipe, Mutation, Store, ViewDefinition, Worker
from aheaddb.live.api import create_live_app
from aheaddb.live.store import decode_snapshot

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from live_reference import reference_features  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--bin-dir', type=Path, required=True)
    args = parser.parse_args()
    data = args.data_dir.resolve(strict=True)
    if not str(data).startswith('/private/tmp/') or (data / '.aheaddb-disposable').read_text().strip() != 'AHEADDB synthetic fault test':
        raise ValueError('Refusing to stop an unmarked or non-temporary cluster')
    dsn = os.environ['AHEAD_TEST_POSTGRES']
    params = conninfo_to_dict(dsn)
    pid_lines = (data / 'postmaster.pid').read_text().splitlines()
    if (params.get('host') != '127.0.0.1' or str(params.get('port')) != pid_lines[3]
            or Path(pid_lines[1]).resolve() != data or params.get('password')):
        raise ValueError('Probe requires the explicitly marked local password-free cluster')
    work = Path('.runtime') / ('database-crash-' + uuid.uuid4().hex[:10])
    work.mkdir(parents=True, exist_ok=False)
    namespace = 'test_crash_' + uuid.uuid4().hex[:10]
    shadow_db = 'ahead_restore_' + uuid.uuid4().hex[:10]
    store = Store(dsn, namespace)
    environment = {**os.environ, 'PGPASSFILE': '/dev/null'}
    pg_options = ['-h', params['host'], '-p', params['port'], '-U', params.get('user') or os.environ.get('USER','')]
    def run(binary, arguments):
        result = subprocess.run([str(args.bin_dir / binary), *arguments], env=environment,
                                capture_output=True, text=True, timeout=120)
        with (work / 'native-tools.log').open('a') as stream:
            stream.write(binary + '\n' + result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(binary + ' failed; see retained task-local log')
    def start():
        run('pg_ctl', ['-D', str(data), '-l', str(work.resolve() / 'postgres-recovery.log'), '-o',
            f'-k {pid_lines[4]} -h 127.0.0.1 -p {pid_lines[3]} -c shared_buffers=64MB -c max_connections=20 '
            '-c track_commit_timestamp=on -c track_io_timing=on -c track_wal_io_timing=on', 'start'])
    def agreement(target):
        for task in demo_tasks():
            response = TestClient(create_live_app(target)).get('/v1/views/' + task.name)
            assert response.status_code == 200
            answer = response.json()
            with target.connect() as conn:
                payload = conn.execute('SELECT payload FROM snapshots WHERE id=%s', (answer['snapshot_id'],)).fetchone()['payload']
            model = target.load_model(answer['model_id'])
            features = reference_features(decode_snapshot(payload), task, answer['prediction_cutoff'])
            np.testing.assert_allclose([answer['predictions'][i] for i in features.index], model.predict(features[model.columns]),
                                       rtol=1e-12, atol=1e-10)
    def edit(name):
        return Edit(request_id=name, mutations=(Mutation(table='orders', key=name, row={
            'id': name, 'entity_id': 'C0000', 'occurred_at': '2024-07-14T12:00:00Z',
            'available_at': '2024-07-14T13:00:00Z', 'amount': 999.25}),))
    stopped, shadow_created = False, False
    try:
        store.migrate()
        with store.connect() as conn:
            schemas = [r['nspname'] for r in conn.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname NOT IN ('information_schema','public')").fetchall()]
            if any(not n.startswith(('ahead_', 'bench_live_', 'test_', 'disk_')) for n in schemas):
                raise ValueError('Unknown schemas present; refusing cluster-level fault injection')
        store.bootstrap(Snapshot(demo_tables(entities=24), demo_schema()))
        for task in demo_tasks():
            model = store.register_model(ModelRecipe(task=task, training_cutoffs=('2024-03-01','2024-04-01'),
                                                     training_as_of='2024-06-01', backend='persistence'))
            store.publish_view(ViewDefinition(model_id=model, cutoff='2024-07-15'))
        worker = Worker(store, 'fifo', lease_seconds=300)
        while worker.once():
            pass
        baseline = store.snapshot()[1].version
        agreement(store)
        dump = work / 'before-crash.dump'
        run('pg_dump', [*pg_options, '-w', '-d', params.get('dbname','postgres'), '-Fc', '-f', str(dump)])
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(shadow_db)))
        shadow_created = True
        run('pg_restore', [*pg_options, '-w', '--no-owner', '-d', shadow_db, str(dump)])
        shadow = Store(make_conninfo(dsn, dbname=shadow_db), namespace)
        assert shadow.snapshot()[1].version == baseline
        agreement(shadow)
        # Backup recovery was proven before the risky operation. The following
        # newly acknowledged source commit must survive WAL recovery independently.
        committed = store.edit(edit('committed-before-database-loss'))
        run('pg_ctl', ['-D', str(data), '-m', 'immediate', 'stop'])
        stopped = True
        outage = TestClient(create_live_app(Store(dsn, namespace))).get('/v1/views/return_14d')
        assert outage.status_code == 503 and outage.json()['code'] == 'source_coverage_unknown'
        start()
        stopped = False
        recovered = Store(dsn, namespace)
        assert recovered.status()['revision'] == committed
        assert 'committed-before-database-loss' in set(recovered.snapshot()[1].table('orders').id)
        assert TestClient(create_live_app(recovered)).get('/v1/views/return_14d').status_code == 503
        replacement = Worker(recovered, 'fifo', lease_seconds=300)
        while replacement.once():
            pass
        agreement(recovered)
        continued = recovered.edit(edit('continued-after-database-loss'))
        while replacement.once():
            pass
        agreement(recovered)
        result = {'backup_restore_verified_before_crash': True,
                  'backup_sha256': hashlib.sha256(dump.read_bytes()).hexdigest(),
                  'postgres_unclean_stop': 'immediate', 'http_outage_status': outage.status_code,
                  'acknowledged_revision_survived': committed, 'continued_revision': continued,
                  'independent_public_generation_agreement': True,
                  'work_folder': str(work), 'postmaster_pid': int((data/'postmaster.pid').read_text().splitlines()[0]),
                  'benchmark_instrumentation': ['track_commit_timestamp','track_io_timing','track_wal_io_timing']}
        print(json.dumps(result, indent=2))
    finally:
        if stopped:
            start()
        with store.connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(namespace)))
        if shadow_created:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(shadow_db)))


if __name__ == '__main__':
    main()

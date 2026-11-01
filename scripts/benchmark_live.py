"""Matched durable HTTP/worker experiments. Freeze inputs after the pilot.

Every policy uses the same versioned source/outbox, snapshot persistence, leases,
publication transaction, HTTP transport and complete-generation consistency.
'current' is explicitly the existing Engine under the same FIFO durability
adapter, algorithmically identical to FIFO. It is not an invented faster control.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import uvicorn
from live_metrics import MacTree, python_usage
from psycopg import sql

from aheaddb import Snapshot
from aheaddb.demo import demo_schema, demo_tables, demo_tasks
from aheaddb.live import Edit, ModelRecipe, Mutation, Store, Unavailable, ViewDefinition, Worker
from aheaddb.live.api import create_live_app
from aheaddb.live.client import Client
from aheaddb.live.store import decode_snapshot
from aheaddb.spec import digest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from live_reference import reference_features  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ['current', 'full', 'fifo', 'debounce', 'budget']


def code_identity():
    paths = sorted((ROOT / 'src/aheaddb/live').glob('*.py')) + [ROOT / 'src/aheaddb' / p for p in (
        'engine.py', 'compiler.py', 'modeling.py', 'snapshot.py', 'spec.py')] + [ROOT / 'scripts/benchmark_live.py', ROOT / 'scripts/live_metrics.py', ROOT / 'tests/live_reference.py']
    return {str(p.relative_to(ROOT)): digest(p.read_text()) for p in paths}


def tasks():
    normal = demo_tasks()
    return [*normal, *(t.model_copy(update={'name': t.name + '_short', 'history_days': 30}) for t in normal)]


def workload(case, count, seed, entities):
    rng = random.Random(seed)
    records = []
    for index in range(count):
        if case == 'dense':
            owners = list(range(entities))
        elif case == 'skew':
            owners = [0 if rng.random() < .85 else rng.randrange(entities)]
        else:
            owners = [rng.randrange(entities)]
        at = (index * .05 if case in {'sparse', 'dense', 'skew'} else
              (index // 12) * .6 if case == 'bursts' else
              index * .001 if case == 'overload' else index * .2)
        records.append({'at_seconds': at, 'index': index, 'rows': [
            {'id': f'B{index:05d}-{owner:04d}', 'entity_id': f'C{owner:04d}',
             'occurred_at': '2024-07-14T12:00:00Z', 'available_at': '2024-07-14T13:00:00Z',
             'amount': round(rng.uniform(1, 600), 8)} for owner in owners]})
    return records


def db_counters(store):
    with store.connect() as conn:
        row = conn.execute("SELECT xact_commit,xact_rollback,blks_read,blks_hit,tup_inserted,tup_updated,tup_deleted,"
                           "temp_bytes,blk_read_time,blk_write_time FROM pg_stat_database WHERE datname=current_database()").fetchone()
        result = {k: float(v) for k, v in row.items()}
        wal = conn.execute("SELECT wal_records,wal_fpi,wal_bytes,wal_buffers_full,wal_write,wal_sync FROM pg_stat_wal").fetchone()
        result.update({k: float(v) for k, v in wal.items()})
        return result


def check_generations(store, client):
    with store.connect() as conn:
        generations = conn.execute("SELECT id,view_name,snapshot_id FROM generations ORDER BY published_at,id,view_name").fetchall()
        payloads = {r['id']: r['payload'] for r in conn.execute("SELECT id,payload FROM snapshots").fetchall()}
    models, snapshots, contexts = {}, {}, {}
    comparisons = 0
    for row in generations:
        answer = client.read(row['view_name'], generation=row['id'], allow_stale=True)
        if row['snapshot_id'] not in snapshots:
            snapshots[row['snapshot_id']] = decode_snapshot(payloads[row['snapshot_id']])
        snapshot = snapshots[row['snapshot_id']]
        if answer.model_id not in models:
            models[answer.model_id] = store.load_model(answer.model_id)
        key = (row['snapshot_id'], answer.task.history_days, answer.prediction_cutoff)
        if key not in contexts:
            contexts[key] = reference_features(snapshot, answer.task, answer.prediction_cutoff)
        model, features = models[answer.model_id], contexts[key]
        expected = model.predict(features[model.columns])
        assert set(answer.predictions) == set(features.index)
        np.testing.assert_allclose([answer.predictions[i] for i in features.index], expected, rtol=1e-12, atol=1e-10)
        comparisons += len(expected)
    return {'generations': len(generations), 'view_generations': len(generations),
            'unique_publication_generations': len({r['id'] for r in generations}),
            'independent_prediction_comparisons': comparisons}


def run_case(spec):
    dsn = os.environ['AHEAD_TEST_POSTGRES']
    namespace = 'bench_live_' + uuid.uuid4().hex[:10]
    store = Store(dsn, namespace)
    with store.connect() as conn:
        if conn.execute("SHOW track_commit_timestamp").fetchone()['track_commit_timestamp'] != 'on':
            raise RuntimeError('Benchmark requires commit timestamp instrumentation on the disposable cluster')
    pg = MacTree(int(os.environ['AHEAD_BENCH_POSTMASTER_PID']))
    setup_started = time.perf_counter()
    setup_python, setup_pg = python_usage(), pg.snapshot()
    store.migrate()
    store.bootstrap(Snapshot(demo_tables(entities=spec['entities'], seed=spec['source_seed']), demo_schema()))
    definitions = tasks()
    for task in definitions:
        mid = store.register_model(ModelRecipe(task=task, training_cutoffs=('2024-03-01', '2024-04-01'),
                                               training_as_of='2024-06-01', backend='persistence'))
        store.publish_view(ViewDefinition(model_id=mid, cutoff='2024-07-15',
                                          freshness_seconds=2 if task.history_days == 30 else .4))
    worker = Worker(store, 'fifo' if spec['policy'] == 'current' else spec['policy'], lease_seconds=300)
    # Identical warm initial publication in every policy; report setup separately.
    warm = Worker(store, 'fifo', lease_seconds=300)
    while warm.once():
        pass
    worker._models = warm._models
    worker._engines = warm._engines
    worker._purge_epoch = warm._purge_epoch
    setup = {'wall_seconds': time.perf_counter() - setup_started,
             'python_cpu_seconds': python_usage()['cpu_seconds'] - setup_python['cpu_seconds'],
             'postgres_cpu_seconds': pg.snapshot()['cpu_seconds'] - setup_pg['cpu_seconds']}
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_live_app(store), host='127.0.0.1', port=port,
                                          access_log=False, log_level='error'))
    server_thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    server_thread.start()
    timeout = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > timeout:
            raise RuntimeError('Local benchmark API did not start')
        time.sleep(.01)
    origin = f'http://127.0.0.1:{port}'
    stop, writer_done = threading.Event(), threading.Event()
    failures, commits, refreshes, reads = [], [], [], []
    silence_proof: dict = {}
    peak_pg = [pg.snapshot()['footprint_bytes']]
    start = time.perf_counter()
    started_wall = datetime.now(timezone.utc).timestamp()
    before_python, before_pg, before_db = python_usage(), pg.snapshot(), db_counters(store)

    def monitor():
        while not stop.wait(.05):
            try:
                peak_pg[0] = max(peak_pg[0], pg.snapshot()['footprint_bytes'])
            except OSError as exc:
                failures.append(type(exc).__name__)
                return

    def write():
        try:
            with Client(origin) as client:
                for operation in spec['operations']:
                    wait = start + operation['at_seconds'] - time.perf_counter()
                    if wait > 0:
                        time.sleep(wait)
                    before = time.perf_counter()
                    revision = client.edit(Edit(request_id=f"bench-{operation['index']}", mutations=tuple(
                        Mutation(table='orders', key=row['id'], row=row) for row in operation['rows'])))
                    commits.append({'revision': revision, 'ack_wall': datetime.now(timezone.utc).timestamp(),
                                    'admission_seconds': time.perf_counter() - before,
                                    'scheduled_seconds': operation['at_seconds'], 'rows': len(operation['rows'])})
                if spec['case'] == 'silence':
                    while store.status()['processed_revision'] < commits[-1]['revision']:
                        if time.perf_counter() - start > spec['timeout_seconds']:
                            raise RuntimeError('Silence setup did not drain')
                        time.sleep(.02)
                    with store.connect() as conn:
                        before_count = conn.execute('SELECT count(DISTINCT id) n FROM generations').fetchone()['n']
                    quiet_start = time.perf_counter()
                    time.sleep(spec.get('silence_seconds', 3))
                    checks = [client.read(task.name).coverage.state for task in definitions]
                    with store.connect() as conn:
                        after_count = conn.execute('SELECT count(DISTINCT id) n FROM generations').fetchone()['n']
                    silence_proof.update(seconds=time.perf_counter()-quiet_start, states=checks,
                                         new_refresh_batches=after_count-before_count)
                    assert all(state == 'fresh' for state in checks) and after_count == before_count
        except Exception as exc:
            failures.append(type(exc).__name__)
        finally:
            writer_done.set()

    def refresh():
        try:
            while not stop.is_set():
                receipt = worker.once()
                if receipt:
                    receipt['publication_ack_wall'] = datetime.now(timezone.utc).timestamp()
                    refreshes.append(receipt)
                elif writer_done.is_set():
                    state = store.status()
                    if state['processed_revision'] == state['revision']:
                        return
                    if any(j['state'] == 'failed' for j in state['jobs']):
                        raise RuntimeError('Poison job in benchmark')
                time.sleep(.005)
        except Exception as exc:
            failures.append(type(exc).__name__)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    writer_thread = threading.Thread(target=write, daemon=True)
    refresh_thread = threading.Thread(target=refresh, daemon=True)
    monitor_thread.start()
    writer_thread.start()
    refresh_thread.start()
    try:
        with Client(origin) as client:
            index = 0
            while index < spec['read_samples']:
                if time.perf_counter() - start > spec['timeout_seconds']:
                    raise RuntimeError('Case exceeded frozen time bound')
                name = definitions[index % len(definitions)].name
                opted = index % 10 == 0
                before = time.perf_counter()
                try:
                    answer = client.read(name, max_age_seconds=2, allow_stale=opted)
                    state = answer.coverage.state
                except Unavailable:
                    state = 'unavailable'
                reads.append({'seconds': time.perf_counter() - before, 'state': state, 'allow_stale': opted})
                index += 1
                time.sleep(.001)
            reads_finished = time.perf_counter()
            while writer_thread.is_alive() or refresh_thread.is_alive():
                if time.perf_counter() - start > spec['timeout_seconds']:
                    raise RuntimeError('Case exceeded frozen drain bound')
                time.sleep(.005)
            writer_thread.join()
            refresh_thread.join()
            if failures:
                raise RuntimeError('Benchmark failed: ' + ','.join(failures))
            end = time.perf_counter()
            after_python, after_pg = python_usage(), pg.snapshot()
            after_db = db_counters(store)
            stop.set()
            monitor_thread.join()
            state = store.status()
            assert state['processed_revision'] == state['revision'] == len(spec['operations']) + 1
            with store.connect() as conn:
                source_commits = {r['revision']: r['t'] for r in conn.execute(
                    "SELECT revision,EXTRACT(EPOCH FROM pg_xact_commit_timestamp(xmin))::double precision t FROM outbox WHERE revision>1").fetchall()}
                publications = conn.execute("SELECT view_name,revision,definition_hash,"
                    "EXTRACT(EPOCH FROM pg_xact_commit_timestamp(xmin))::double precision t FROM generations WHERE revision>1").fetchall()
                deadlines = {(r['view_name'], r['revision']): r['t'] for r in conn.execute(
                    "SELECT view_name,revision,EXTRACT(EPOCH FROM deadline)::double precision t FROM jobs WHERE revision>1").fetchall()}
                storage = conn.execute("SELECT sum(pg_total_relation_size(quote_ident(table_schema)||'.'||quote_ident(table_name))) n "
                                       "FROM information_schema.tables WHERE table_schema=%s", (namespace,)).fetchone()['n']
            lags, misses = [], {t.name: 0 for t in definitions}
            for commit in commits:
                for task in definitions:
                    first = min(r['t'] for r in publications if r['view_name'] == task.name and r['revision'] >= commit['revision'])
                    source_time = source_commits[commit['revision']]
                    if first is None or source_time is None or first < source_time:
                        raise RuntimeError('Missing/nonmonotonic commit clock; case retained as unavailable measurement')
                    lag = first - source_time
                    deadline = deadlines[(task.name, commit['revision'])]
                    misses[task.name] += int(first > deadline)
                    lags.append({'view': task.name, 'revision': commit['revision'], 'seconds': lag,
                                 'durable_deadline': deadline, 'publication_commit': first,
                                 'source_commit': source_time, 'deadline_missed': first > deadline,
                                 'edit_ack_delay_seconds': commit['ack_wall']-source_time,
                                 'scheduled_to_publication_seconds': first-(started_wall+commit['scheduled_seconds'])})
            result = {'case': spec['case'], 'policy': spec['policy'], 'repeat': spec.get('repeat', 0),
                      'setup': setup, 'wall_seconds': end - start, 'source_commits': len(commits),
                      'source_rows_written': sum(c['rows'] for c in commits), 'reads': reads,
                      'read_sampling': 'fixed-count closed-loop sample, not continuous time availability',
                      'post_sample_drain_seconds': max(0,end-reads_finished), 'silence_proof': silence_proof,
                      'commits': commits, 'source_to_publication': lags, 'deadline_misses': misses,
                      'refreshes': refreshes, 'final_revision': state['revision'],
                      'no_starved_task': state['processed_revision'] == state['revision'],
                      'python_cpu_seconds': after_python['cpu_seconds'] - before_python['cpu_seconds'],
                      'postgres_cpu_seconds': after_pg['cpu_seconds'] - before_pg['cpu_seconds'],
                      'python_peak_rss_bytes': after_python['peak_rss_bytes'],
                      'postgres_peak_sampled_footprint_bytes': peak_pg[0],
                      'database_counter_delta': {k: after_db[k] - before_db[k] for k in before_db},
                      'retained_relation_bytes': int(storage),
                      'io_notes': 'WAL bytes/records/syncs and buffer counters count persistence; I/O timing may be disabled; not physical device throughput', 'host_load_average': os.getloadavg(),
                      'started_wall': started_wall, 'accounting': 'Mach timebase calibrated; PostgreSQL root plus reaped/live children',
                      'limits': ['CPU snapshots can race short-lived children; final sample taken after drain',
                                 'PostgreSQL peak footprint sampled at50ms; shared memory accounting is platform-dependent',
                                 'Source lag/deadlines use instrumented PostgreSQL commit timestamps and actual persisted job deadlines',
                                 'Fixed read samples may finish before drain; post-sample drain reported separately',
                                 'Current numerical-engine durability adapter is the FIFO algorithm',
                                 'Synthetic reliability/performance evidence is not predictive validation']}
            # All public retained-generation responses, identical snapshot/cutoff/model.
            # Verification is outside the measured serving window, reported separately.
            verify_start = time.perf_counter()
            result['agreement'] = check_generations(store, client)
            result['agreement']['wall_seconds'] = time.perf_counter() - verify_start
            return result
    finally:
        stop.set()
        writer_thread.join(timeout=10)
        refresh_thread.join(timeout=10)
        server.should_exit = True
        server_thread.join(timeout=10)
        sock.close()
        with store.connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(namespace)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-file', type=Path)
    parser.add_argument('--pilot', action='store_true')
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/live/benchmark')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    protocol_path = args.output / 'protocol.json'
    if args.case_file:
        print(json.dumps(run_case(json.loads(args.case_file.read_text())), allow_nan=False))
        return
    if args.pilot:
        for policy in ('full', 'fifo', 'budget'):
            spec = {'case': 'pilot', 'policy': policy, 'entities': 64, 'source_seed': 8031,
                    'read_samples': 80, 'timeout_seconds': 180,
                    'operations': workload('bursts', 8, 1731, 64)}
            path = args.output / f'pilot-{policy}-input.json'
            path.write_text(json.dumps(spec, indent=2))
            subprocess.run([sys.executable, __file__, '--case-file', str(path)], check=True,
                           stdout=(args.output / f'pilot-{policy}.json').open('x'))
            print(f'pilot {policy} saved', flush=True)
    if args.freeze:
        if protocol_path.exists():
            raise ValueError('Protocol already exists; preserve frozen evidence')
        if not all((args.output / f'pilot-{p}.json').exists() for p in ('full', 'fifo', 'budget')):
            raise ValueError('Measure the pilot baseline before freezing confirmation inputs')
        protocol = {'version': 1, 'frozen_at': datetime.now(timezone.utc).isoformat(), 'code': code_identity(),
                    'policies': POLICIES, 'repeats': 3, 'entities': 64, 'source_seed': 28471,
                    'read_samples': 1000, 'timeout_seconds': 300, 'silence_seconds': 3,
                    'cases': {case: workload(case, 120 if case != 'silence' else 12, 70193, 64)
                              for case in ('sparse', 'dense', 'skew', 'bursts', 'silence', 'overload')},
                    'notes': ['Each baseline retains shared compatible features and identical persistence',
                              'current and fifo are algorithmically identical adapters, not independent methods',
                              'p95 serving latency requires >=1000 observations/case; no p99 superiority claim',
                              'No policy tuning after freezing; failures and censored cases retained']}
        protocol_path.write_text(json.dumps(protocol, indent=2))
        print('Confirmation protocol frozen', flush=True)
    if args.run:
        protocol = json.loads(protocol_path.read_text())
        if protocol['code'] != code_identity():
            raise ValueError('Code changed after protocol freeze; retain and explicitly version the experiment')
        runs = [(case, policy, repeat) for case in protocol['cases'] for policy in protocol['policies']
                for repeat in range(protocol['repeats'])]
        random.Random(50177).shuffle(runs)
        for index, (case, policy, repeat) in enumerate(runs):
            name = f'{case}-{policy}-{repeat}'
            destination = args.output / (name + '.json')
            if destination.exists():
                try:
                    saved = json.loads(destination.read_text())
                    if saved['agreement']['generations'] > 0 and saved['no_starved_task']:
                        continue
                except (ValueError, KeyError):
                    pass
                raise RuntimeError('Existing incomplete/failed case retained; explicit recovery required')
            spec = {**{k: protocol[k] for k in ('entities', 'source_seed', 'read_samples', 'timeout_seconds', 'silence_seconds')},
                    'case': case, 'policy': policy, 'repeat': repeat, 'operations': protocol['cases'][case]}
            case_path = ROOT / '.runtime' / 'live-benchmark-inputs' / (name + '.json')
            case_path.parent.mkdir(parents=True, exist_ok=True)
            case_path.write_text(json.dumps(spec))
            with destination.open('x') as stream, (args.output / (name + '-stderr.txt')).open('x') as error:
                result = subprocess.run([sys.executable, __file__, '--case-file', str(case_path)], stdout=stream, stderr=error)
            print(f'{index+1}/{len(runs)} {name} exit={result.returncode}', flush=True)
            if result.returncode:
                raise RuntimeError('Retained failed confirmation case; investigate before proceeding')


if __name__ == '__main__':
    main()

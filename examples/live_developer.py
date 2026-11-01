"""Fresh-source developer walkthrough using only the public HTTP SDK.

The API and worker must already run against an empty dedicated Live namespace.
This script generates its own public synthetic rows; it reads no private files.
"""
from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

from aheaddb import Event, Outcome, Schema, Task
from aheaddb.live import Edit, ModelRecipe, Mutation, Unavailable, ViewDefinition
from aheaddb.live.client import Client
from aheaddb.live.contracts import Bootstrap, EvaluationRequest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8790')
    args = parser.parse_args()
    rng = random.Random(77193)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    accounts = [{'account_id': f'U{i:02d}', 'registered_at': start.isoformat()} for i in range(24)]
    activity = []
    for index, account in enumerate(accounts):
        for day in range(240):
            if rng.random() < .025 + .16 * index / 23:
                instant = start + timedelta(days=day, hours=12)
                activity.append({'event_id': f'E{len(activity):06d}', 'account_id': account['account_id'],
                                 'event_time': instant.isoformat(), 'seen_at': (instant+timedelta(hours=1)).isoformat(),
                                 'amount': round(rng.uniform(10, 200), 2)})
    schema = Schema(entity_table='accounts', entity_key='account_id', available_at='registered_at',
                    events=(Event(table='activity', key='event_id', entity_key='account_id',
                                  occurred_at='event_time', available_at='seen_at', values=('amount',)),))
    tasks = [Task(name='renewal_14d', kind='binary', outcome=Outcome(table='activity'),
                  horizon_days=14, history_days=60, metric='roc_auc'),
             Task(name='expected_value_14d', kind='regression',
                  outcome=Outcome(table='activity', aggregation='sum', column='amount'),
                  horizon_days=14, history_days=60, metric='mae')]
    cutoff = '2024-07-15'
    def wait(client, revision):
        until = time.monotonic()+120
        while time.monotonic()<until:
            try:
                answers = [client.read(task.name, max_age_seconds=30) for task in tasks]
                if all(answer.coverage.revision >= revision for answer in answers):
                    return answers
            except Unavailable:
                pass
            time.sleep(.05)
        raise RuntimeError('Worker did not publish the required source revision')
    with Client(args.base_url, timeout=60) as client:
        initial = client.bootstrap(Bootstrap(**{'schema': schema.model_dump(),
                                               'tables': {'accounts': accounts, 'activity': activity}}))
        evaluations = []
        for task in tasks:
            recipe = ModelRecipe(task=task, training_cutoffs=('2024-03-01','2024-04-01','2024-05-01'),
                                 training_as_of='2024-06-01', backend='persistence')
            evaluation = client.evaluate(EvaluationRequest(recipe=recipe,
                backtest_cutoffs=('2024-03-01','2024-04-01','2024-05-01','2024-06-01'),
                labels_as_of='2024-08-25', prediction_cutoff=cutoff))
            evaluations.append({'task': task.name, 'revision': evaluation.revision,
                                'folds': len(evaluation.report['folds']), 'snapshot_id': evaluation.snapshot_id})
            model = client.register_model(recipe)
            client.publish_view(ViewDefinition(model_id=model, cutoff=cutoff))
        before = wait(client, initial)
        changed = client.edit(Edit(request_id=str(uuid.uuid4()), mutations=(Mutation(table='activity', key='changed-input', row={
            'event_id': 'changed-input', 'account_id': 'U00', 'event_time': '2024-07-14T12:00:00Z',
            'seen_at': '2024-07-14T13:00:00Z', 'amount': 900.0}),)))
        after = wait(client, changed)
        assert before[1].predictions['U00'] != after[1].predictions['U00']
        for old, current in zip(before, after, strict=True):
            assert old.coverage.source_id == current.coverage.source_id
            assert old.model_version == current.model_version
            assert old.prediction_cutoff == current.prediction_cutoff
            assert old.data_version != current.data_version
        print(json.dumps({'synthetic': True, 'initial_revision': initial, 'changed_revision': changed,
            'evaluations': evaluations, 'features': [{'task': new.task.name, 'before': old.predictions['U00'],
                'after': new.predictions['U00'], 'source_id': new.coverage.source_id,
                'covered_revision': new.coverage.revision, 'generation': new.generation,
                'model_version': new.model_version, 'cutoff': new.prediction_cutoff,
                'state': new.coverage.state} for old,new in zip(before,after,strict=True)]}, indent=2))


if __name__ == '__main__':
    main()

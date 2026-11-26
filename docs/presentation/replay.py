from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import uuid
from importlib.metadata import version

import aheaddb
from aheaddb import Snapshot
from aheaddb.demo import demo_tables,demo_schema,demo_tasks
from aheaddb.live import Store,Worker,ModelRecipe,ViewDefinition,Edit,Mutation,Unavailable
from aheaddb.live.maintenance import backup,restore

def source_identity():
    script = Path(__file__).resolve()
    checkout = script.parents[2]
    package = Path(aheaddb.__file__).resolve().parent
    files = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(package.rglob("*.py"))}
    evidence = {
        "source_revision": None,
        "reference_source_revision": "72f6fc8f956749940c933ea92c0913d54110bbdb",
        "replay_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "package_version": version("aheaddb"),
        "package_python_sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    if package != checkout / "src/aheaddb" or not (checkout / ".git").exists():
        return evidence
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
        paths = ["docs/presentation/replay.py", "src/aheaddb"]
        changed = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--", *paths],
                                          cwd=checkout, text=True)
    except (OSError, subprocess.CalledProcessError):
        return evidence
    if not changed:
        evidence["source_revision"] = head
    return evidence


parser=argparse.ArgumentParser(description="Replay synthetic source coverage in a fresh owned namespace")
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();public=args.output.resolve()
dsn=os.environ.get('AHEAD_LIVE_DSN')
if not dsn:raise SystemExit('Set AHEAD_LIVE_DSN to a dedicated disposable PostgreSQL source')
identity=source_identity()
public.mkdir(mode=0o700,parents=True,exist_ok=False)
namespace='presentation_'+uuid.uuid4().hex[:12]
store=Store(dsn,namespace);store.migrate();store.bootstrap(Snapshot(demo_tables(entities=24),demo_schema()))
for task in demo_tasks():
 mid=store.register_model(ModelRecipe(task=task,training_cutoffs=('2024-03-01','2024-04-01'),training_as_of='2024-06-01',backend='persistence'));store.publish_view(ViewDefinition(model_id=mid,cutoff='2024-07-15'))
worker=Worker(store,'fifo')
def drain():
 results=[]
 while receipt:=worker.once():results.append(receipt)
 return results
def answers():return {task.name:store.read(task.name).model_dump(mode='json') for task in demo_tasks()}
initial_jobs=drain();before=answers();initial=store.status();req='presentation-order-450';edit=Edit(request_id=req,mutations=(Mutation(table='orders',key='presentation-order',row={'id':'presentation-order','entity_id':'C0000','occurred_at':'2024-07-14T00:00:00Z','available_at':'2024-07-14T01:00:00Z','amount':450}),));revision=store.edit(edit);pending=store.status();refusals={}
for task in demo_tasks():
 try:store.read(task.name);raise AssertionError('uncovered read admitted')
 except Unavailable as e:refusals[task.name]=str(e)
older={task.name:store.read(task.name,allow_stale=True).model_dump(mode='json') for task in demo_tasks()};assert all(a['coverage']['state']=='stale' for a in older.values());assert store.edit(edit)==revision;assert store.status()['revision']==revision
badrev=store.status()['revision']
try:store.edit(Edit(request_id='invalid-owner',mutations=(Mutation(table='orders',key='invalid-owner',row={'id':'invalid-owner','entity_id':'orphan','occurred_at':'2024-07-14T00:00:00Z','available_at':'2024-07-14T01:00:00Z','amount':450}),)));raise AssertionError('orphan admitted')
except ValueError as e:invalid=str(e)
assert store.status()['revision']==badrev
recovery_jobs=drain();after=answers();assert after['revenue_14d']['predictions']['C0000']!=before['revenue_14d']['predictions']['C0000'];assert all(a['coverage']['revision']==revision for a in after.values());reopened=Store(dsn,namespace);assert reopened.read('revenue_14d').generation==store.read('revenue_14d').generation
backupfile=public/'synthetic-backup.json';ledger=public/'synthetic-purges.jsonl';digest=backup(store,backupfile,ledger);oldhash=hashlib.sha256(backupfile.read_bytes()).hexdigest()
try:backup(store,backupfile,ledger);raise AssertionError('backup clobbered')
except FileExistsError:pass
assert hashlib.sha256(backupfile.read_bytes()).hexdigest()==oldhash
restored=Store(dsn,namespace+'_restore');restore(restored,backupfile,ledger);restored_status=restored.status()
try:restored.read('revenue_14d');raise AssertionError('unknown restored clock admitted')
except Unavailable:pass
record={**identity,'synthetic':True,'seed':19,'entities':24,'selected_entity':'C0000','order_amount':450,'cutoff':'2024-07-15','initial_status':initial,'before':before,'edit_receipt':revision,'pending_status':pending,'refusals':refusals,'explicit_older':older,'recovery_jobs':recovery_jobs,'after':after,'invalid_edit_refusal':invalid,'idempotent_retry_revision':revision,'fresh_store_same_generation':True,'backup_payload_digest':digest,'backup_existing_preserved':True,'restore_status':restored_status}
(public/'journey.json').write_text(json.dumps(record,indent=2,default=str)+'\n');print(json.dumps({'pass':True,'revision':revision,'before':before['revenue_14d']['predictions']['C0000'],'after':after['revenue_14d']['predictions']['C0000'],'restored_clock_unknown':restored_status['clock_unknown']}))

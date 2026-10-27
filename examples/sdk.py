"""Run with `python examples/sdk.py` after installing AHEADDB."""
from aheaddb import Compiler, Engine, Snapshot, fit
from aheaddb.demo import demo_schema, demo_tables, demo_tasks

schema = demo_schema()
snapshot = Snapshot(demo_tables(), schema)
task = demo_tasks()[0]
model = fit(snapshot, task, ["2024-03-01", "2024-04-01", "2024-05-01"], "2024-06-01")
engine = Engine(snapshot)
print(Compiler(snapshot).plan(task, "2024-07-15"))
print(engine.materialize(task, model, "2024-07-15").head())
print(engine.refresh(snapshot))  # Same source and clock: no predictions recomputed.
print(engine.inspect(task.name))

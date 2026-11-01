"""Claim -> durable snapshot -> compute -> fenced atomic generation publication."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from ..compiler import Compiler
from ..engine import Engine
from ..spec import Task, digest
from .contracts import Answer, Coverage, ViewDefinition
from .planner import Candidate, Policy, choose, context_key
from .store import Conflict, Store, decode_snapshot


@dataclass
class Claim:
    jobs: list[dict]
    snapshot_id: str
    payload: dict
    source_as_of: str
    observed_at: str
    purge_epoch: int
    source_id: str


class Worker:
    def __init__(self, store: Store, policy: Policy = "budget", lease_seconds: float = 30,
                 max_attempts: int = 3, fault=None):
        if not 0 < lease_seconds <= 300 or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid worker bounds")
        self.store, self.policy = store, policy
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts
        self.fault = fault or (lambda boundary: None)
        self._engines: dict[str, Engine] = {}
        self._models: dict = {}
        self._purge_epoch: int | None = None

    def claim(self) -> Claim | None:
        self.fault("claim_before_transaction")
        with self.store.connect() as conn, conn.transaction():
            meta = self.store._lock(conn)
            if self._purge_epoch != meta['purge_epoch']:
                self._engines.clear()
                self._models.clear()
                self._purge_epoch = meta['purge_epoch']
            if meta["clock_unknown"]:
                return None
            conn.execute("UPDATE jobs SET state='failed',error='Attempt limit exhausted after worker loss' "
                         "WHERE state='running' AND lease_until<=clock_timestamp() AND attempts>=%s", (self.max_attempts,))
            rows = conn.execute("SELECT j.*,v.definition,v.task,v.fence FROM jobs j JOIN views v ON v.name=j.view_name "
                                "WHERE j.definition_hash=v.definition_hash AND j.attempts<%s AND "
                                "(j.state='pending' OR (j.state='running' AND j.lease_until<=clock_timestamp())) "
                                "AND NOT EXISTS(SELECT 1 FROM jobs busy WHERE busy.view_name=j.view_name "
                                "AND busy.state='running' AND busy.lease_until>clock_timestamp()) ORDER BY j.id LIMIT 10000",
                                (self.max_attempts,)).fetchall()
            costs = {r["context"]: max(0.001, r["seconds"] * 2) for r in conn.execute("SELECT * FROM costs").fetchall()}
            candidates = []
            for row in rows:
                context = context_key(meta["schema_doc"], row["task"], row["definition"])
                row["context"] = context
                candidates.append(Candidate(row["id"], row["view_name"], row["revision"], row["queued_at"],
                                            row["deadline"], context, costs.get(context, 0.1)))
            ids = choose(candidates, meta["now"], self.policy)
            if not ids:
                return None
            selected = [r for r in rows if r["id"] in ids]
            revision = selected[0]["revision"]
            sid, snapshot = self.store._snapshot(conn, revision, meta)
            for row in selected:
                token = conn.execute("UPDATE views SET fence=fence+1 WHERE name=%s RETURNING fence",
                                     (row["view_name"],)).fetchone()["fence"]
                conn.execute("UPDATE jobs SET state='running',token=%s,lease_until=clock_timestamp()+"
                             "(%s * interval '1 second'),attempts=attempts+1,snapshot_id=%s WHERE id=%s",
                             (token, self.lease_seconds, sid, row["id"]))
                row["token"] = token
            successor = conn.execute("SELECT authored_at FROM outbox WHERE revision=%s", (revision + 1,)).fetchone()
            from .store import encode_snapshot
            claim = Claim(selected, sid, encode_snapshot(snapshot),
                          str(successor["authored_at"] if successor else meta["now"]),
                          str(meta["now"]), meta["purge_epoch"], meta["source_id"])
            self.fault("claim_before_commit")
        self.fault("claim_after_commit")
        return claim

    def compute(self, claim: Claim) -> tuple[dict[str, Answer], dict]:
        self.fault("compute_before")
        snapshot = decode_snapshot(claim.payload)
        desired = {j["view_name"]: j["definition_hash"] for j in claim.jobs}
        if self._purge_epoch != claim.purge_epoch:
            self._engines.clear()
            self._models.clear()
            self._purge_epoch = claim.purge_epoch
        key = digest(desired)
        cache = self._engines.get(key)
        reusable = self.policy != "full" and cache is not None
        if reusable:
            assert cache is not None
            receipt = cache.refresh(snapshot, mode="incremental")
            engine = cache
        else:
            engine = Engine(snapshot)
            for job in claim.jobs:
                definition = ViewDefinition.model_validate(job["definition"])
                if definition.model_id not in self._models:
                    if len(self._models) >= 128:
                        self._models.clear()
                    self._models[definition.model_id] = self.store.load_model(definition.model_id)
                model = self._models[definition.model_id]
                engine.materialize(Task.model_validate(job["task"]), model, definition.cutoff)
            receipt = {"prediction_rows": sum(len(v.predictions) for v in engine.views.values()),
                       "feature_rows": sum(len(x) for x in engine.contexts.values()), "cache_restored": False}
        generation = str(uuid.uuid4())
        answers = {}
        for job in claim.jobs:
            view = engine.views[job["view_name"]]
            definition = ViewDefinition.model_validate(job["definition"])
            answers[job["view_name"]] = Answer(
                generation=generation, task=view.task, task_version=view.task.version,
                model_id=definition.model_id, model_version=view.model_version,
                preprocessing=Compiler.PREPROCESSING, prediction_cutoff=view.cutoff,
                snapshot_id=claim.snapshot_id, data_version=snapshot.version,
                coverage=Coverage(source_id=claim.source_id, state="fresh", revision=job["revision"], current_revision=job["revision"],
                                  source_as_of=claim.source_as_of, observed_at=claim.observed_at, age_seconds=0),
                published_at=claim.observed_at,
                predictions={str(k): float(v) for k, v in view.predictions["prediction"].items()})
        if len(self._engines) >= 4:
            self._engines.pop(next(iter(self._engines)))
        self._engines[key] = engine
        self.fault("compute_after")
        return answers, receipt

    def publish(self, claim: Claim, answers: dict[str, Answer], receipt: dict, seconds: float) -> None:
        self.fault("publish_before_transaction")
        with self.store.connect() as conn, conn.transaction():
            meta = self.store._lock(conn)
            if meta["clock_unknown"] or meta["purge_epoch"] != claim.purge_epoch:
                raise Conflict("Clock/purge epoch changed; publication fenced")
            if set(answers) != {j["view_name"] for j in claim.jobs}:
                raise Conflict("Incomplete generation")
            # Check every member before writing any result. One transaction includes
            # predictions, view pointers, dedup states and min consumer progress.
            for job in claim.jobs:
                row = conn.execute("SELECT j.state,j.token,j.lease_until,v.fence,v.definition_hash,v.generation "
                                   "FROM jobs j JOIN views v ON v.name=j.view_name WHERE j.id=%s FOR UPDATE OF j,v",
                                   (job["id"],)).fetchone()
                if (not row or row["state"] != "running" or row["token"] != job["token"]
                        or row["fence"] != job["token"] or row["definition_hash"] != job["definition_hash"]
                        or row["lease_until"] <= meta["now"]):
                    raise Conflict("Expired, cancelled or replaced worker; publication fenced")
                current = conn.execute("SELECT revision FROM generations WHERE id=%s AND view_name=%s",
                                       (row["generation"], job["view_name"])).fetchone()
                if current and current["revision"] > job["revision"]:
                    raise Conflict("Older work cannot overwrite a newer generation")
            for job in claim.jobs:
                answer = answers[job["view_name"]].model_copy(update={"published_at": str(meta["now"])})
                if (answer.snapshot_id != claim.snapshot_id or answer.coverage.revision != job["revision"]
                        or answer.task.name != job["view_name"]):
                    raise Conflict("Generation metadata differs from claimed work")
                conn.execute("INSERT INTO generations(id,view_name,revision,definition_hash,snapshot_id,answer) "
                             "VALUES(%s,%s,%s,%s,%s,%s)", (answer.generation, job["view_name"], job["revision"],
                                job["definition_hash"], claim.snapshot_id, Jsonb(answer.model_dump(mode="json"))))
                conn.execute("UPDATE views SET generation=%s WHERE name=%s", (answer.generation, job["view_name"]))
                conn.execute("UPDATE jobs SET state='done',error=NULL WHERE id=%s", (job["id"],))
                conn.execute("UPDATE jobs SET state='superseded' WHERE view_name=%s AND definition_hash=%s "
                             "AND revision<=%s AND state IN ('pending','failed')",
                             (job["view_name"], job["definition_hash"], job["revision"]))
            self.store._update_progress(conn)
            conn.execute("INSERT INTO costs VALUES(%s,%s,1) ON CONFLICT(context) DO UPDATE SET "
                         "seconds=costs.seconds*0.75+excluded.seconds*0.25,samples=costs.samples+1",
                         (claim.jobs[0]["context"], seconds))
            self.fault("publish_before_commit")
        self.fault("publish_after_commit")

    def once(self) -> dict | None:
        start = time.perf_counter()
        claim = self.claim()
        if claim is None:
            return None
        try:
            answers, receipt = self.compute(claim)
            seconds = time.perf_counter() - start
            self.publish(claim, answers, receipt, seconds)
            return {"generation": next(iter(answers.values())).generation, "snapshot_id": claim.snapshot_id,
                    "revision": claim.jobs[0]["revision"], "views": sorted(answers),
                    "seconds": time.perf_counter() - start, **receipt}
        except Exception:
            # Error text can contain source values. Persist a fixed code, never an
            # arbitrary exception message or DSN. Process death leaves a leased job.
            with self.store.connect() as conn, conn.transaction():
                self.store._lock(conn)
                for job in claim.jobs:
                    conn.execute("UPDATE jobs SET state=CASE WHEN attempts>=%s THEN 'failed' ELSE 'pending' END,"
                                 "error='Refresh failed; inspect local worker exception' WHERE id=%s AND token=%s AND state='running'",
                                 (self.max_attempts, job["id"], job["token"]))
            raise

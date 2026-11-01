# Launch draft for AHEADDB Live 0.3.0

Draft only. Do not post until the public source, versioned release, CI and clean
clone/download checks pass. The measured comparison is complete, with mixed
results and substantial host variation; do not turn it into a general speed claim.

## Project post

I built AHEADDB Live to make an application's predictions answer a practical
question: which committed source records does this result actually cover?

It wraps my existing Python statistical engine with an owned PostgreSQL source,
a transactional outbox and a recoverable refresh worker. The Python SDK returns
the task, model, information cutoff, source revision and generation. If a worker
falls behind or source coverage becomes unknown, current reads fail. Older values
need an explicit opt-in.

The example app uses shared orders for two different features: customer return
probability and expected revenue. In the installed demo I commit an order, kill
the worker after computation, then recover both features at the acknowledged
source revision. The data is synthetic; the separate temporal predictive-quality
reports keep their original baselines and limitations.

The local checks include 144 passing tests, independently recomputed public
generations, abrupt worker/source process loss, a real full volume and unclean
PostgreSQL recovery. All 90 frozen cases completed, with more than 1.6 million
independent prediction comparisons. Under matched persistence, budget planning
lowered median deadline misses versus FIFO in six workloads, but measured CPU
improved in only four and tight deadlines were often missed. Debouncing remained
competitive. Equivalent controls varied substantially on the shared Mac, so these
observations do not establish a general performance advantage.

This is a local development layer, with bounded data and one API/worker. It does
not provide production CDC, authentication or high availability.

[Source and walkthrough](https://github.com/nazeeh111/AHEADDB).

## Short version

AHEADDB Live gives prediction results an explicit committed source boundary.
Owned PostgreSQL edits, durable refresh jobs and fenced publication keep the
Python SDK honest when records change or a worker dies. The installed two-feature
demo, recovery checks and consistency limits are documented in the repository.
Synthetic serving tests stay separate from predictive-quality evidence.

## Technical discussion prompt

The tradeoff I would like feedback on is the owned-source boundary: a revision
lock makes source edits and outbox progress straightforward to reason about,
but it serializes admission. The benchmark counts that cost, along with snapshot
persistence, reads and refresh work. What application sizes or update patterns
would make that boundary useful before adopting an external CDC system?

Share only in a relevant community when the owner chooses to post. Do not send
unsolicited messages, invent users/adoption or describe the local experiment as a
production deployment.

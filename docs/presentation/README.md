# An order, the uncovered interval and recovery

This completed local example uses current source `72f6fc8f956749940c933ea92c0913d54110bbdb`, Python 3.13.11 and the declared locked packages. PostgreSQL 16.2 ran in a new task-owned cluster with a Unix socket only. No existing database, credentials, private customer record, model download or paid service was used.

## Read the result

The synthetic shop has 24 customers, seed 19 and a fixed July 15, 2024 information cutoff. Models use the explicit `persistence` backend, trained at March 1 and April 1 with June 1 training maturity. The observed C0000 expected-revenue output is in synthetic shop units:

1. Source revision 1 is covered by the first published generation: **97.3**.
2. One 450-unit July14 order commits source revision 2. Before the worker runs, both normal reads refuse with `Committed source changes are not covered`. `allow_stale=True` explicitly retrieves revision 1, marked `stale`.
3. The worker publishes complete outputs covering revision 2. C0000 becomes **315.0**. Task, model and cutoff remain fixed. A new Store instance reopens the same generation.

[Actual machine record](journey.json) · [Source-derived wide figure](coverage.svg) · [Portrait figure](coverage-mobile.svg). The renderer reads these actual outputs; it is a technical state illustration, not a product screenshot. It makes no predictive-quality or model-improvement claim.

The real SignalDesk browser then records a separate second 450-unit order: source 2→3, expected revenue 315→420. While its worker is stopped, the UI hides both outputs; explicit older-result opt-in displays the uncovered revision 2. Worker recovery restores current revision 3. A zero amount is rejected by the form without an order request. Desktop 1280 px and fresh 390 px viewport captures show actual UI. [Browser record](browser-journey.json) · [Waiting](worker-wait.png) · [Recovered desktop](recovered-desktop.png) · [Recovered phone](recovered-mobile.png).

## Replay against a new disposable source

Follow the [current repository quickstart](https://github.com/nazeeh111/AHEADDB/blob/72f6fc8f956749940c933ea92c0913d54110bbdb/docs/live-quickstart.md) to provision a **dedicated disposable PostgreSQL 15+ source** and install the frozen `live` extra. Set `AHEAD_LIVE_DSN` to that owned source. From the repository root:

```sh
uv sync --frozen --extra live
uv run --no-sync python docs/presentation/replay.py --output /tmp/ahead-replay-new
```

The output path must not exist. The script creates unique synthetic namespaces, executes source/edit/refusal/recovery and writes a fresh data-only backup and ledger. It does not connect to any source unless you supply its connection. It leaves its synthetic namespaces for inspection; destroy the disposable cluster when finished. UUIDs, generations, timestamps and backup digests vary per run. Compare the source revision, state transitions, fixed task/model/cutoff, and 97.3→315.0 values, rather than expecting identical run bytes.

## Reopen the captured data-only boundary

[Download the complete evidence bundle](evidence.zip). `bundle-manifest.json` identifies each included file by exact SHA256. The source package/runtime is external and explicitly pinned above; executable application code is not copied into the evidence ZIP.

The delivered `synthetic-backup.json` and `synthetic-purges.jsonl` are data-only synthetic artifacts captured at revision 2. The backup contains its payload digest. Restore requires the source-bound current ledger with owner-only permissions, so after extracting use `chmod 600 synthetic-purges.jsonl`. A new namespace must be empty. Use the documented `ahead live restore` route with your own dedicated source. The actual run reopened the capture in a fresh namespace and observed `clock_unknown: true`; do not expose restored predictions as fresh before the documented owner-controlled clock acknowledgement and refresh. A backup does not know about subsequent edits, and an old ledger cannot prove later deletions.

The run also checks that repeating the exact edit request returns revision 2, that an invalid orphan-customer edit preserves the source revision, and that exporting to an existing backup path refuses without changing its bytes. These are bounded software checks, not exactly-once behavior across source replacement, production authentication, external CDC, high availability, continuous WAL/PITR or general forecast validation.

## Provenance

The complete application source and all existing research/license/attribution files are unchanged. The published engineering branch is already an ancestor of current main; no held branch is overlaid. Historical public-data studies and v0.3.0 recordings remain historical and do not validate the current v4 compiler's predictive quality. The new synthetic path complements them without resealing those older results.

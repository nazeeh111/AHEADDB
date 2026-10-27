# Independent core correctness review

Scope: the direct entity/event execution contract in `spec.py`, `snapshot.py`, `compiler.py`, `modeling.py` and `engine.py`. Reviewed against temporal validity, complete refresh equivalence and bounded input handling. This is a focused engineering review, not a whole-project security audit or evidence about arbitrary relational queries.

## Method

`tests/test_core.py` uses deterministic numeric and binary estimators whose predictions depend on every compiled aggregate. A reference path independently compiles the complete new snapshot and calls each model. Maintained views must equal that reference after each update, with the same entity membership, cutoff and model/data identities.

Controls cover:

- Source insertion, numeric update, deletion and foreign-key movement. A moved record invalidates both old and new entity owners.
- Entity insertion/deletion, including deleting every entity and event.
- New records previously absent from context, late availability and changes outside the active history window.
- Future events and events unavailable at the observation cutoff; strict left-open/right-closed history and outcome boundaries.
- Label maturity and reporting delay, plus future/late-row invariance of an actually fitted histogram model.
- Shared aggregate contexts across distinct tasks, separate model prediction work, model mutation, preprocessing-version changes and clock advancement.
- Full recomputation, ordinary whole-source caching and incremental mode result agreement.
- Atomic refresh failure, defensive source/result copies, task/schema mismatch, orphan references, row limits and backtest label embargo.
- Thirty reproducible Hypothesis examples of mixed update streams, each containing up to twelve insert/update/delete/move operations, checked against full recompilation after every step.
- Exact calibration-bin edges, missing numeric outcomes and nonfinite thresholds.

## Findings addressed

The initial implementation's numeric-label aggregation treated missing outcome values as zero. The compiler now rejects missing numeric outcomes in the eligible outcome interval; missing features remain represented by explicit missing-count aggregates. A regression control verifies both behaviors.

Nonfinite outcome thresholds could silently classify every label as negative. The public specification now rejects NaN and infinite numeric values. Regression controls exercise all three nonfinite threshold cases.

A suspected floating-point calibration-bin overlap did not reproduce. Exact edge probabilities from zero through one form a partition in the executed test; no defect is claimed.

The review's first backtest fixture supplied twelve samples where the documented fitting minimum is twenty. That was a test-fixture error; it was corrected to twenty-four entities, without weakening the fitting guard.

## Evidence and limits

Initial combined core run: 25 passed in 202.68 seconds, including all 30 generated update-stream examples, corrected embargo, actual histogram-training leakage, empty-view and exact-bin-edge controls. Five controls added after that run began were executed separately: missing numeric outcome (1), nonfinite thresholds (3), and schema-change rejection (1), all passed. That revision contained 30 test cases. Ruff passed. Eight pandas FutureWarnings from concatenating empty frames were reported; no test was skipped. Final release-wide validation and CI must run the complete current suite.

The supported dependency graph is a star with direct event-to-entity foreign keys. There is no nearest-neighbor retrieval or arbitrary multihop query cache; this review establishes no correctness claim for either. Outside-window changes can conservatively recompute their owner, and clock changes recompute every active entity. Changed-source diff discovery compares every indexed source key, so reduced recomputed prediction work alone does not establish lower end-to-end latency.

An event log's absence-as-zero label semantics require the caller to provide a complete observation interval. The source format does not preserve bitemporal correction history: overwriting an old row cannot recreate its former value for an old backtest. Availability timestamps and immutable historical snapshots must reflect the information actually available. Calibration is measured, not promised.


## Follow-up execution and baseline review

A focused follow-up inspected the final refresh modes, diagnostics, backend selection and immutable row-index cache. The current core test file contains 44 cases. Fourteen added controls passed in targeted runs. The five controls affected by the final noop optimization were rerun and passed; the final immutable-index control passed separately. Ruff passed. Complete final-suite and release CI evidence belong to the project's validation receipts.

Full mode now skips incremental diff discovery and always recomputes active features and predictions. Ordinary cache mode also skips diff discovery; a changed whole-snapshot identity invalidates the complete view, while an unchanged source can reuse aggregate context. Incremental mode compares cached row-hash indexes only when the snapshot identity changes. Equal immutable identities avoid both diff and feature scans. A source change, clock advancement, model update and intervening noops were exercised sequentially in all three modes, with predictions compared to independent full feature compilation after each step.

The review found that the first noop-cache implementation still compiled empty feature requests, which built source-length masks. This was corrected to reuse the existing context directly. Controls now fail if either cached mode calls diff discovery or feature compilation on an unchanged source. A model-only update must still recompute its predictions while reusing unchanged features.

Additional controls verify that changed model task/schema metadata is rejected atomically in every mode, training-range diagnostics are visible and included in model identity, explicit per-task baseline selection is honored, the default histogram model is retained for other tasks, and unknown task keys in backend selection are rejected.

The final snapshot implementation computes SHA256 row indexes once after ingestion validation and normalization. A control mutates a returned hash Series and verifies internal identity remains intact; another rejects any repeated row hashing during a changed-source incremental refresh. Source loading, validation and row-index creation remain real costs. They are measured separately from refresh latency, with retained index storage reported. A refresh-only benchmark must not be interpreted as complete database-to-prediction latency. The optional PostgreSQL adapter requires its separate actual-database CI gate; this focused review did not execute a local PostgreSQL server.

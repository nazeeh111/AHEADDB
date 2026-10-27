# Release verification

Historical v0.1 validation; [current v0.2 evidence](v020-results.md).
Version 0.1.0 has separate evidence for core correctness, numerical quality, UI, optional backends and publication. Source reports are not substituted for actual execution.

## Local checks

- Final complete core/integration run: 48 passed, 7 skipped, 3 upstream deprecation warnings. Six skips are the separately executed FastDFS tests; one is the explicitly configured PostgreSQL service test. Additional actual Parquet round-trip/row-limit and documented positional-demo/preservation controls passed after the complete run was collected.
- Optional FastDFS: 6 tests passed in its isolated pinned environment. Public-data held-out entity/cutoff keys and labels align exactly with the core comparisons.
- Independent core review: 44 test cases, including 30 generated mixed-update streams, temporal leakage, label maturity/embargo, cached-hash defense, transactional failure, model/preprocessing changes and all-mode agreement. See [review evidence and limits](core-review.md).
- Ruff and mypy passed for the final source. JSON/CSV checks independently recompute reported metrics and compare held-out label alignment; synthetic refresh has 90 comparisons with exact reference agreement.
- Actual HTTP app, CLI exports, read-only source preservation, task plans, source provenance, synthetic refresh and MCP stdio initialization/tool discovery/calls were executed. SDK-only Parquet restoration matched the DuckDB snapshot version exactly.
- Built wheel and source distribution; installed the wheel in a fresh isolated environment. The packaged static inspector and fitted predictions/refresh API are exercised there, independently of editable source imports.

Saved machine-readable receipts are in [`artifacts/validation`](../artifacts/validation). Optional expensive public-data and FastDFS runs remain outside routine CI. The default core environment needs no optional model runtime.

## UI and media

Current-source SignalDesk was rendered at 1440 px and 390 px. All exercised API requests returned 200; no JavaScript errors or horizontal overflow. Automated WCAG-tag axe scans found 0 violations at both widths after fixing rank contrast. Keyboard focus was checked. This is bounded automated accessibility evidence, not a complete accessibility certification.

The 26.92-second VP8 WebM is an authentic browser capture of source reload, predictions, provenance, task inspection, a synthetic order update and chronological backtest. It is labeled recorded playback. The original capture is preserved as `assets/demo.webm`; an H.264 MP4 conversion is supplied for compatibility. Both were fully decoded successfully. Desktop/mobile screenshots were visually inspected. Charts were rendered from saved numerical records and visually inspected; they include negative outcomes.

The capture used the same numerical computation as this release. Later source-index/no-op optimizations affect refresh execution cost, not its prediction values; its displayed single-call timing is explicitly not a benchmark. Benchmark timings come only from the saved reproducible benchmark run.

## PostgreSQL and public release gates

No user's database is contacted. The first public CI run passed 50 tests, including the real PostgreSQL checks, with six optional FastDFS skips. Its later CLI quickstart step exposed a positional-destination bug, which was fixed and regression-tested before release. CI provisions a fresh PostgreSQL 16 service, installs the optional adapter, checks bounded snapshot reads/transaction recovery, and deliberately attempts a persistent write inside its read-only transaction. The public release must wait for this gate and all other CI steps to pass. The local machine has no PostgreSQL server, so a local skip alone does not verify this connector.

After publication, repository visibility, exact remote commit, workflow conclusion, versioned release, wheel/source/demo/results assets and public documentation links are verified separately. A clean clone/install/example/API check is part of delivery. Current remote evidence belongs in release notes and the final delivery receipt; do not infer it from local tests.

## Material limits

Read [architecture](architecture.md), [data assumptions and failures](data.md) and [refresh tradeoffs](benchmarks.md). The execution graph is a direct star, the snapshot/index lives in RAM, and source availability/complete observation intervals must be supplied honestly. Source membership is not causal explanation. Raw probabilities and unconstrained numerical estimates need application-specific validation. No foundation-model result, broad model superiority, public hosted service or production deployment is claimed.

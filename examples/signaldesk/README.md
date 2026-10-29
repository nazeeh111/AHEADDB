# SignalDesk

A small working shop application consumes AHEADDB's actual HTTP API: customers receive14-day return probabilities and expected revenue; selecting a customer shows the exact history record membership. Adding a synthetic order refreshes the affected customer's two predictions and carries proven unchanged results into the new version. The source database stays unchanged.

```sh
uv sync --frozen
uv run --no-sync ahead demo run/signaldesk
uv run --no-sync ahead serve --project run/signaldesk/ahead.json
```

Open http://127.0.0.1:8787. The seed 19 dataset is original synthetic data; its scores are not public-dataset results. The server refits models on startup and holds maintained views in RAM. It does not send email, contact customers or change a business system.

The API is documented at http://127.0.0.1:8787/docs. Browser requests use `/api/state`, `/api/predictions/{task}`, `/api/provenance/{task}/{entity_id}`, `/api/backtest/{task}`, `/api/plan/{task}` and `/api/demo/change`. `/api/refresh` reloads the configured original DuckDB snapshot. The inspector is intentionally specific to this example; generic library projects should consume the SDK or HTTP result structure using their own application vocabulary.

[Current v0.2 recorded playback](../../assets/demo.webm) shows real local browser interactions. [Desktop screenshot](../../assets/signaldesk.png) and [mobile screenshot](../../assets/signaldesk-mobile.png) come from the current application. These are recordings, not a hosted live service or speed benchmark.

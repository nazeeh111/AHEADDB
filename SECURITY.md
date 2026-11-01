# Security

AHEADDB Live is a local development service with an owned PostgreSQL source.
Its bootstrap, edit, model/view registration, maintenance and demo commands write
the explicitly configured dedicated Live schema. Ordinary application tables are
not automatically enrolled or changed. Use a dedicated database role and namespace;
keep production credentials and source rows out of examples, logs and reports.

The Live API is unauthenticated and intended for loopback use only. The CLI binds
to `127.0.0.1`; the API validates local HTTP hosts/origins and bounds request bodies.
The Python client accepts local HTTP endpoints and ignores proxy environment
settings. These controls do not provide multi-user authentication, authorization,
TLS, or protection from other local processes. Do not expose the service publicly.

Source edits are parameterized and validated against the declared data schema.
The default snapshot limits are 100,000 rows and 64 MiB of decoded stored JSON.
Worker leases/fences and publication transactions protect generation integrity;
they do not authorize external effects. The service accepts declarative model
recipes and data-only snapshots, refits models with pinned numerical dependencies,
and checks their identity. It does not load executable untrusted pickles, arbitrary
Python or client-supplied SQL. Treat database contents as private data.

Portable backups contain private source and training data. Keep backups and the
current source-bound purge ledger private. Restore requires an empty namespace and
an owner-controlled offline cutover. A stale ledger cannot prove later deletions;
restoring a copy does not propagate purges to other running copies. See the
[consistency and recovery contract](docs/live-design.md) before using maintenance.

The preserved v0.2 library and compatibility inspector have a different scope:
only explicitly supplied DuckDB/Parquet files are ingested, with bounded rows and
DuckDB memory, and that inspector's synthetic update changes in-memory data.
Parquet is opened through DuckDB without a pandas Arrow engine. Neither API exposes
arbitrary commands or file paths. No LLM, API key or paid API is part of numerical
execution.

Native database/Parquet parsers and numerical libraries still process input.
Use trusted files and sandbox hostile inputs. Row and payload limits do not bound
every temporary allocation; this is not a hardened multi-tenant service. Keep
pinned dependencies under review for compatible security updates.

Report vulnerabilities privately through the repository owner's GitHub profile
before publishing exploitable details. Exclude private rows and credentials.

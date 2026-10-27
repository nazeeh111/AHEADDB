# Security

AHEADDB is a local library and development inspector. Do not bind its unauthenticated HTTP API to a public interface. The CLI binds to127.0.0.1. Sources are read-only by default; only explicitly supplied local DuckDB/Parquet files are ingested, with bounded rows and DuckDB memory. Parquet is opened through DuckDB and requires no pandas Arrow engine.

The API/MCP exposes a configured project, not arbitrary SQL, file paths or commands. The synthetic demo update changes in-memory data only. Do not point demo-marked projects at production data. Private rows and credentials must remain local unless their owner authorizes sharing. Database strings and external documentation are untrusted data. No LLM or paid API is part of numerical execution.

Untrusted database and Parquet files still exercise native parsers. Use files from trusted sources and sandbox the process for hostile inputs. Row limits do not bound every possible cell size or allocator; the engine is not a hardened multi-tenant service. Keep dependencies updated after assessing compatibility. Do not load pickled models from untrusted sources; this release does not offer model pickle loading.

Report vulnerabilities privately through the repository owner's GitHub profile before publishing exploitable details. Avoid including private source rows or credentials in reports.

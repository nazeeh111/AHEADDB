# Licenses and attribution

AHEADDB source is Apache 2.0, with the required license and NOTICE in the repository. No upstream ML checkpoint or code is copied into the source tree. Installing dependencies obtains separate distributions under their own terms. `uv.lock` records pinned distribution versions/hashes; it is not a replacement for upstream license files.

| Component | Upstream license | Role |
|---|---|---|
| [DuckDB](https://github.com/duckdb/duckdb/blob/main/LICENSE) | MIT | Bounded local snapshot reads |
| [NumPy](https://github.com/numpy/numpy/blob/main/LICENSE.txt) | BSD3-Clause | Numerical arrays |
| [pandas](https://github.com/pandas-dev/pandas/blob/main/LICENSE) | BSD3-Clause | Temporal tables/aggregates |
| [scikit-learn](https://github.com/scikit-learn/scikit-learn/blob/main/COPYING) | BSD3-Clause | Fitted CPU baselines/boosting/ExtraTrees |
| [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) | MIT | Explicit validated specifications |
| [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) | MIT | Local API |
| [Starlette](https://github.com/encode/starlette/blob/master/LICENSE.md) | BSD3-Clause | HTTP implementation |
| [Uvicorn](https://github.com/encode/uvicorn/blob/main/LICENSE.md) | BSD3-Clause | Local serving |
| [Typer](https://github.com/fastapi/typer/blob/master/LICENSE) | MIT | CLI |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) | MIT | Optional stdio tools |
| [FastDFS](https://github.com/HKUSHXLab/fastdfs) | Apache 2.0 | Optional released relational feature comparison |
| [Featuretools](https://github.com/alteryx/featuretools/blob/main/LICENSE) / [Woodwork](https://github.com/alteryx/woodwork/blob/main/LICENSE) | BSD3-Clause | Optional FastDFS components |
| [SQLGlot](https://github.com/tobymao/sqlglot/blob/main/LICENSE) | MIT | Optional FastDFS SQL compilation |

See [research attribution](THIRD_PARTY_RESEARCH.md) for inspected model projects and their separate licenses. The RT-J, RDBLearn, STEER, InkStream and RIPPLE papers/repositories are research references, not bundled runtimes. No pretrained model is included and no zero-shot transfer claim is made. Matplotlib charts are generated from saved results; Playwright/FFmpeg are capture tools, not product runtime dependencies.

Public datasets and derived evaluation records retain CC BY 4.0; full attribution, changes, provenance hashes and assumptions are in [data.md](data.md). Generated synthetic data and application demo are AHEADDB project artifacts. The Python wheel/sdist includes this project's LICENSE/NOTICE; third-party wheels include their own license files. If redistributing a complete installed environment, retain all dependency licenses and notices as well.

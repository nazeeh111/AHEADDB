# Research and scope

Primary-source review: 26 October 2026. This is an implementation decision record, not a claim that AHEADDB invented relational prediction, predictive SQL, caching, or incremental inference. Upstream benchmark numbers have not been independently reproduced here. See the [source revisions and licenses](THIRD_PARTY_RESEARCH.md).

## What already exists

| System | Established capability | Consequence for AHEADDB |
| --- | --- | --- |
| [Kumo Engine](https://github.com/NVIDIA/kumo-engine) | Open local engine for connected-record retrieval, task training, evaluation and batch predictions; Apache-2.0 owned code | AHEADDB cannot claim that local relational prediction is new, or describe Kumo as cloud-only. Its documented 1.0.0 wheel targets Linux x86-64; NVIDIA GPU and SSD storage are recommended for training. |
| [KumoRFM-2](https://arxiv.org/abs/2604.12596) and [Structured Data Models](https://github.com/NVIDIA/structured-data-models) | Released GPU-oriented model implementations, including relational models | A separate substantial model ecosystem. This release does not establish superiority over it. |
| [TabPFN-Rel](https://github.com/PriorLabs/tabpfn-rel) / [RelArena-α](https://github.com/PriorLabs/relarena) | Relational feature synthesis, local or hosted estimators, task YAML, temporal tuning, reusable feature caches and a relational predictive interface | Task specifications and agent integration are established patterns. Hosted inference consumes quota; local weights have separate access conditions. No hosted route is needed here. |
| [RDBLearn](https://github.com/HKUSHXLab/rdblearn) | Relational flattening with FastDFS followed by a scikit-learn-compatible base estimator | A strong conventional comparison; its full environment includes Torch, AutoGluon and TabPFN and requires Python 3.12. |
| [MindsDB](https://github.com/mindsdb/mindsdb) | Established database-facing AI and predictive-model interfaces | Predictive SQL predates this project. The current [SQL documentation](https://mindsdb.github.io/engine/) emphasizes semantic search and agents, so older CREATE MODEL documentation must not be mistaken for current product coverage. |

## Research foundations

[RT-J](https://arxiv.org/abs/2610.17867) is a released relational transformer, not a language model substituting guesses for statistical inference. The official [code](https://github.com/stanford-star/relational-transformer) supports task-specific contexts and released checkpoints. Its [checkpoint card](https://huggingface.co/stanford-star/rt-j) lists Apache-2.0 weights, approximately 85.6 million parameters and pretraining on PluRel plus the Join. The paper rounds the model size to 86 million. Training data has separate terms.

The official [quickstart](https://github.com/stanford-star/relational-transformer/blob/main/notebooks/quickstart.ipynb) explicitly checks for Ampere-or-newer CUDA hardware, uses bf16 attention and recommends A100/L4. Its default batch is described as about 9 GiB GPU memory. These are upstream requirements, not locally measured minimums. A CPU-only Mac run is not a verified fallback. RT-J is therefore a documented optional research comparison, with no fabricated local score. Pretraining overlap with any evaluated public database would need a corpus audit before a zero-shot transfer claim.

[RelBench](https://github.com/stanford-star/relbench) provides tasks and database metadata with temporal evaluation conventions. Current v3 loading differs from older examples. The library's Apache-2.0 license does not establish permission to redistribute every underlying database. AHEADDB records dataset-specific provenance separately.

[STEER](https://arxiv.org/abs/2610.00907) changes sampling probabilities using a once-per-task schema relevance ranking. It reduces sampled context, according to the authors' experiments. This does not establish that cached activations can be shared between different task-conditioned models, nor that sampling neighborhoods remain unchanged after an insertion.

Its [released implementation](https://github.com/lids-lab/steer) includes task rankings, so reproducing a shipped configuration does not itself require another LLM request. Generating a new ranking is an optional billed-provider API route and is excluded from AHEADDB's numerical workflow. The documented host is Linux with an NVIDIA GPU; model-specific dependencies and original notices remain applicable.

[InkStream](https://github.com/WuDan0399/InkStream) and [RIPPLE++](https://arxiv.org/abs/2601.12347) are distinct incremental GNN inference projects. The supplied 2601.12347 identifier belongs to RIPPLE++, not InkStream. InkStream's README points to [2309.11071](https://arxiv.org/abs/2309.11071), describes a modified PyTorch Geometric environment, and demonstrates mixed insert/delete streams. No reusable license was identified in its root listing or repository metadata, so its code is not copied. RIPPLE++ explicitly studies changed graph features and inserted/deleted vertices and edges. These establish prior art for selective propagation; their speedups cannot be transferred to this different execution path.

The [RIPPLE repository](https://github.com/dream-lab/ripple) exposes separate full-recomputation and incremental workloads and declares Apache-2.0 in its README. Its setup recipe uses Python 3.10.12, Torch and DGL CUDA wheels. The extended paper also describes native NumPy CPU inference and reports a 128 GiB workstation setup, with higher memory use for incremental state in some cases. These are source reports, not an AHEADDB reproduction; the README setup is not a verified lightweight Mac dependency path.

## Feasible comparison on a laptop

[FastDFS](https://github.com/HKUSHXLab/fastdfs), the released flattening component behind RDBLearn, can run without neural model weights. A bounded CPU comparison uses FastDFS 0.2.1 with an ExtraTrees estimator. This is a relational aggregate pipeline, not a pretrained relational foundation model or a replica of full RDBLearn results. Its package requires pandas below 3, PyArrow below 21 and SQLGlot 27.x; it runs in an isolated environment. The upstream API accepts explicit foreign keys, event time columns and per-row cutoffs.

AHEADDB additionally prefilters each cutoff using both occurrence and availability time and excludes all undeclared entity attributes. FastDFS alone has a single event-time field, so relying on its cutoff flag alone would not establish information availability. Future outcome rows and target label columns are never passed into feature generation; historical rows of the outcome event table can contribute within the guarded history window. Preprocessing learns only from training rows and prediction columns are frozen to the fitted feature set.

The comparison adapter has its own `fit_fastdfs` and `predict(snapshot, cutoff)` interface. Maintained-view serving currently uses the core baseline/boosting backends. FastDFS is an optional evaluated comparison, not an interchangeable maintained-view neural backend. The verified environment is recorded in [requirements-fastdfs.lock](../requirements-fastdfs.lock). Reproduce it separately from the core environment:

```bash
uv venv --python 3.12 .runtime/fastdfs
uv pip sync --python .runtime/fastdfs/bin/python requirements-fastdfs.lock
PYTHONPATH=src OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  .runtime/fastdfs/bin/python -m pytest tests/test_fastdfs.py -q
PYTHONPATH=src OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  .runtime/fastdfs/bin/python scripts/evaluate_fastdfs.py --dataset all
```

Fetch the public snapshots first using the public-data reproduction instructions. Woodwork in this released dependency stack still imports deprecated `pkg_resources`; the separate environment pins setuptools 80.9.0 to keep that import working. Upstream deprecation warnings are visible during optional checks.

The optional run constrains the ExtraTrees estimator to one worker and sets native numerical-library thread environment variables to one. The released DFS2SQL dependency opens its own DuckDB connections without an explicit thread setting; its feature-query parallelism is not controlled by this adapter. Runtime results therefore compare the documented execution policies, not precisely equal CPU or floating-point-operation budgets. The adapter also refuses a real file named `:memory:` in the working directory, because this upstream version treats the DuckDB sentinel as a potential filesystem path before opening it.

## Narrow engineering contribution to test

The implemented hypothesis is that source-diff membership can identify affected entities for the supported one-hop aggregate plans, reusing unaffected entity features and model predictions. Correctness is checked against full recomputation before reporting latency. Cross-task reuse is limited to compatible, task-independent feature computations. A change to history windows, cutoff, preprocessing or a model invalidates affected computation conservatively.

An insertion can add an entity to a computation even when no previously selected row changed. A deletion must consider old membership. Foreign-key updates must consider old and new entities. Late availability and clock advancement can change eligible records without a source-value update. Any reported result is scoped to the supported plans, dataset sizes and measured streams; it is not a general incremental compiler or a new foundation model.

## Deferred comparisons

[Griffin](https://github.com/yanxwb/Griffin) publishes graph-centric model code and checkpoint instructions, but its documented transfer path uses GPU-oriented training scripts and additional geometric/text dependencies. [OpenRFM](https://github.com/T-Lab/OpenRFM) publishes a smaller independent synthetic-pretraining experiment, but explicitly limits its model to a three-table chain and reports a substantial gap to the original target. Neither is substituted silently for RT-J, nor given an invented AHEADDB evaluation score. Hardware and dependency constraints are reported instead of purchasing compute.

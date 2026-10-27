# Upstream source record

Inspected on 26 October 2026 through public upstream source and package metadata. Commit identities were read from GitHub's public repository/commit API. Moving branch links below are navigation aids; the recorded commit is the reviewed identity. Dependency distribution notices are covered separately by the project's license/notice files. Research references do not imply endorsement.

| Upstream | Reviewed revision | License / reuse decision |
| --- | --- | --- |
| [Relational Transformer](https://github.com/stanford-star/relational-transformer) | `def35f92bf890741c4be41c4c315886365ee5ffb`, package metadata 1.10.0 | Apache-2.0 source. Released RT-J weights also declare Apache-2.0; training data has separate terms. Documentation reference, no vendored code or weights. |
| [RDBLearn](https://github.com/HKUSHXLab/rdblearn) | `3c5c30f52c45a85ad8605fde57ec495a4c165487`, package metadata 0.1.2 | Apache-2.0. Reference for relational flattening. Full package not required. |
| [FastDFS](https://github.com/HKUSHXLab/fastdfs) | `f51ffb01e5b1cf3874dfe7a343bddf7bc94d48a3`, package metadata 0.2.1 | Apache-2.0. Optional installed package, no copied implementation. Note that source `__version__` says 1.2; the distribution pin is 0.2.1. |
| [RelBench](https://github.com/stanford-star/relbench) | `f263ba4222e255110a8f5ae058803a5675c6f4fc` | Apache-2.0 library. Underlying databases require their own license/provenance checks. |
| [InkStream](https://github.com/WuDan0399/InkStream) | `5d3597dc5842bcdf125680b4b012f53ac28b8f86`, default branch `release` | No root license identified and API license field was null. Research reference only; no code copied. |
| [STEER](https://github.com/lids-lab/steer) | `d4dbe2240f3b125ac8bccb38593b1cf801f2897a` | Apache-2.0 owned source; bundled model code, checkpoints and data retain separate terms. Reference only. Paper PDF has separate CC BY-NC-ND 4.0 terms. |
| [RIPPLE / RIPPLE++](https://github.com/dream-lab/ripple) | `002016df9518f451354dded9096ffb7c52e3ed60` | README explicitly declares Apache-2.0, copyright DREAM:Lab 2025; GitHub's parsed license field was null. Reference only, no code copied. |
| [DuckDB](https://github.com/duckdb/duckdb) | `c1606652f2a0ff1fd2ce9bf90373575abaf85767` | MIT. Mature database dependency; installed package version is pinned by AHEADDB, independently of this research branch revision. |
| [SQLGlot](https://github.com/tobymao/sqlglot) | `feb0e08aba28a29590596bb3f5761fbaeb0e98b9` | MIT. Mature SQL parser dependency; installed version is pinned independently. |
| [NVIDIA Kumo Engine](https://github.com/NVIDIA/kumo-engine) | `39619849a1a0f363df8699862d8a87ad043f3fe5` | Apache-2.0 owned source, multiple separate vendored licenses/notices. Comparison reference, no copied source. |
| [TabPFN-Rel](https://github.com/PriorLabs/tabpfn-rel) | `86315dd164a0153bf1b0d49015a226785c06a6eb` | Apache-2.0 package; model access/weight terms are separate. Comparison reference, no model download. |
| [Griffin](https://github.com/yanxwb/Griffin) | `b9d0e1fa8d89dfb1cd8bd5976b71de8a3b515427`, branch `main-public` | Apache-2.0 source. Checkpoint/data terms require separate checks before reuse. Documentation reference only. |
| [MindsDB](https://github.com/mindsdb/mindsdb) | `3f7d3c690295c52b64f512333c59465d20bce0b9` | Current root license is MIT. Comparison reference, no copied source. |
| [RelArena-α](https://github.com/PriorLabs/relarena) | `87ce6e749dcd48ab829855bb0c17437e6f1a803f` | Apache-2.0 benchmark framework. Model and dataset terms are separate. |
| [NVIDIA Structured Data Models](https://github.com/NVIDIA/structured-data-models) | `1e2061bf51176c1e87f8facb16212abf60aca4a2` | Apache-2.0 source; externally retrieved models/materials keep separate terms. Reference only. |
| [OpenRFM](https://github.com/T-Lab/OpenRFM) | `d7b667a8a9694fe8785c287138fc4e5aab34af4b` | MIT source. Checkpoint card inspected as a reference; no code/weights imported. |

## Paper identities

- Ranjan et al., [RT-J: Large-Scale Pretraining of Relational Transformers for Context-Efficient Predictions](https://arxiv.org/abs/2610.17867), v3 dated 15 October 2026.
- Mohamed and Aboulnaga, [STEER: Reducing Inference Cost in Relational Foundation Models through Semantically Informed Sampling](https://arxiv.org/abs/2610.00907), v1 dated 1 October 2026.
- Naman et al., [RIPPLE++: An Incremental Framework for Efficient GNN Inference on Evolving Graphs](https://arxiv.org/abs/2601.12347), submitted 18 January 2026.
- Wu et al., [InkStream: Instantaneous GNN Inference on Dynamic Graphs via Incremental Update](https://arxiv.org/abs/2309.11071), linked by the official InkStream repository.
- [RelArena-α, TabPFN-Rel and RPI](https://arxiv.org/abs/2608.16319), linked by the official RelArena and TabPFN-Rel repositories.

## Reproduction boundary

This inspection verified source identity, documented interfaces and stated requirements. It did not reproduce upstream paper scores, establish full patent clearance, certify licenses of every transitive dataset, or audit foundation-model pretraining membership. AHEADDB's own saved evaluation artifacts identify which backend actually executed. An unavailable GPU backend has no local quality result.

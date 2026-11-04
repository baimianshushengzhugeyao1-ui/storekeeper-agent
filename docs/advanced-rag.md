# Multilingual hybrid retrieval

## Problem and scope

The original 18-section English corpus returns three vector neighbours for every
question, including questions the corpus cannot answer. The known synthetic v1
baseline scored 60% Recall@3 on ten Chinese queries. This slice keeps that
retriever intact and adds an opt-in evidence pipeline, not a new knowledge graph
or a claim of general agent autonomy.

## Implemented pipeline

`advanced_retrieval.py` builds a three-node LangGraph subgraph:

1. `rewrite_query`: for Chinese queries, use the configured model to translate
   the search query into English, preserving numbers, negation and subquestions.
   Keep the original question for evidence grading and answering. English
   queries skip this call. Translation can drift; both versions are logged.
2. `hybrid_retrieve`: fetch up to eight dense neighbours using the existing
   Chroma/MiniLM index, and independently score heading chunks with local BM25.
   Fuse by reciprocal rank, `sum(1 / (60 + rank))`; do not add incompatible raw
   BM25 and cosine scores. Deduplicate by chunk ID. BM25 uses a simple English
   tokenizer with conservative plural normalization, not a Chinese tokenizer.
3. `grade_evidence`: a structured LLM decision grades candidate relevance and
   coverage of the original question. It selects a minimal set of at most three
   chunks and copies exact source quotes. Python rejects unknown/duplicate IDs,
   quotes differing beyond whitespace and over-budget sets. Unsupported questions return no
   evidence. This is LLM evidence grading/reranking, **not a cross-encoder model**.

The evidence call does not see reference labels. The fixed relevance cutoff is
2/3; no thresholds were tuned on reported results. Exact quote checks verify
provenance, not semantic correctness. A model can still select an irrelevant
but authentic quotation or miss supporting evidence. It is not a full factuality
validator or prompt-injection guarantee.

The application routes empty evidence or retrieval errors to
`escalated_to_human`, without calling the policy answerer. The existing final
draft step still uses a model; no claim of complete answer-level grounding is
made. Business eligibility rules and order-writing approval paths are unchanged.

## Run

From the repository directory, configure the existing DeepSeek key locally in
`.env` (never commit it), then:

```sh
uv run python scripts/index_policies.py
uv run python -m storekeeper.live_preview --rag enhanced --port 8766
```

The preview uses real models and fixture orders. Each policy question adds at
most one translation and one grading call before answering, with no unlimited
rewrite loop. Provider timeout is 45 seconds. The new pipeline retries connection
or timeout failures at most once per stage; it does not retry semantic or schema
failures, and it never retries order writes. Transport errors are logged. The
app's coarse process lock still serializes work; this is not high-concurrency
serving. Local offline demo on port 8765 remains a deterministic fixture demo
and does not demonstrate the enhanced live pipeline.

Alternatively set `POLICY_RETRIEVAL_MODE=enhanced` in `.env`. The default remains
`baseline` for an explicit rollback and a reproducible comparison. Invalid
values fail rather than silently changing strategy.

## Evaluation

```sh
uv run python scripts/evaluate_advanced_retrieval.py --freeze-only --run-directory var/evaluations/my-rag-run
uv run python scripts/evaluate_advanced_retrieval.py --live --run-directory var/evaluations/my-rag-run --workers 3
```

`--live` sends public policy chunks and synthetic questions to the configured
DeepSeek service and consumes API credit. No Shopify credentials are used. A
manifest freezes questions, labels, corpus, core retriever hashes and settings
before execution; completed reports cannot be overwritten. Tracing export is
disabled for this runner.

Four variants isolate dense-only, hybrid-only, translated hybrid, and the full
evidence pipeline. v1 is previously seen regression data. The additional v2 set
has eight English questions, eight Chinese pairs and four unanswerable probes,
authored with Codex and frozen before this run. It is not independently annotated
or real customer traffic. Compare evidence Recall@3/MRR and no-answer
abstentions separately. Precision on variable-length selected evidence is
reported with its actual denominator; compare it with fixed-three retrieval
carefully. Tokens and latency include added grading calls. This is retrieval
evaluation, not final-answer accuracy or actual business success.

### Recorded first run, transport recovery and quote replay

See [measured results and limitations](advanced-rag-results.md). The first live
run contained eleven transport failures. `recover_retrieval_transport.py`
retries only those failures and writes a separate report. An exact quote check
also falsely rejected Markdown line wrapping; `recheck_retrieval_evidence.py`
replays saved model outputs using whitespace-only normalization. It changes no
model decisions and makes no new calls. Three v2 cases changed. Because v2 exposed
this bug, it is not untouched validation of the fix. First-run evidence and
recovery/replay lineage are retained. A final application smoke checks one
Chinese supported question and one unanswerable question with live calls.

## References

- [Hybrid retrieval and RRF](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)
- [RRF formula](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking)
- [LangGraph custom RAG patterns](https://docs.langchain.com/oss/python/langgraph/agentic-rag)

These are design references; this implementation uses local Chroma and Python,
not Azure Search. Neither hybrid retrieval nor query translation is a new
invention. The contribution is integrating and evaluating them on this fork's
specific cross-language and insufficient-evidence failures.

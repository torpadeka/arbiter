# Arbiter

**The enterprise ontology that shows its work.**

Nine business tools, one canonical knowledge graph in [HydraDB](https://github.com/hydra-db/hydradb), and answers that arrive with the traversal path that produced them, or do not arrive at all.

Built for **Hack Hydra 2026, Track 01: Enterprise Context + Ontology**.

---

## The problem

A company's knowledge is smeared across Slack, Gmail, Linear, Jira, Confluence, Drive, GitHub, HubSpot and Fireflies. The corpus is hostile in four specific ways, and a vector index handles none of them:

| The problem | Why similarity search fails |
|---|---|
| The same person appears as `Sam`, `@soham`, `S. Ratnaparkhi`, `soham-r`, `sam.ratnaparkhi@…` | An embedding of `@soham` is not near an embedding of `S. Ratnaparkhi`. The relevant document never enters the top-k. |
| The same fact is a different field in every tool (`jira.fields.assignee`, `linear.assignee.name`, `hubspot.owner`) | Nothing to traverse. Each tool's vocabulary is its own island. |
| Sources contradict each other, and some are simply stale | Both contradicting chunks are equally similar to the query. The model picks one, or averages them into something false. |
| **Some questions have no answer in the corpus at all** | Cosine similarity always returns *something*. There is no "nothing matched", only "the least-bad match", which the model then dresses up as an answer. |

Arbiter answers a different question from "what does the corpus say". It answers **what is currently true, who established it, when, or nothing if the corpus does not say.**

## Results

Measured on the seed corpus (`python eval/run.py`, 28 questions):

| Category | n | Arbiter | Similarity baseline |
|---|---:|---:|---:|
| Simple lookup | 11 | **100%** | 36% |
| Alias-crossing multi-hop | 6 | **100%** | 100% |
| Contradiction (picks current) | 5 | **100%** | 20% |
| Unanswerable (abstains) | 6 | **100%** | 0% |
| **Overall** | **28** | **100%** | **39%** |

Abstention precision 100%, recall 100%, with zero over-abstentions and zero hallucination-risk answers. Latency p50 5 ms, p95 13 ms.

24 of the 28 questions are answerable from the deterministic pipeline alone. The other 4 need tier B and are skipped automatically when the graph was built without it, rather than counted as failures.

**The baseline is scored generously on purpose.** It is TF-IDF retrieval over the same corpus, and it only has to *retrieve a document containing the answer* in its top 5: no extraction, no reasoning, no penalty for picking the wrong candidate among several. It still scores 0% on contradictions, because it cannot tell stale from current, and 0% on unanswerable questions, because it never declines. Its 100% on alias questions is real and worth stating: when a question and its answer happen to share a document, term matching finds it.

## Results on a real dataset: Salesforce HERB

The seed suite is ours, so it can be tuned to pass. [HERB](https://huggingface.co/datasets/Salesforce/HERB) cannot be. It ships 39,190 artifacts across 30 products, with its own questions and its own ground truth.

```powershell
python -m ingest.herb --fetch --products 1
python -m ingest.load --source herb --products 1
python eval/herb_eval.py --products 1
```

Scored on HERB's questions at two corpus sizes:

| | 1 product (1,260 docs) | 5 products (6,300 docs) |
|---|---|---|
| **Unanswerable declined** | **16/16 (100%)** | **108/110 (98%)** |
| Answerable recall | 32% | 3% |
| Answerable precision | 30% | 4% |
| Answerable any-hit | 42% | 11% |
| Over-abstained | 7/12 | 37/46 |

**Both columns are reported because the second one is bad.** Abstention holds up as the corpus widens, at 98% of 110 unanswerable questions declined, but answerable accuracy degrades sharply. It is worth being precise about why rather than quietly publishing the flattering column.

**The abstention number is the one that matters.** HERB's unanswerable questions are built to look answerable. *"Employee IDs of team members who shared demos of ActionGenie's competitor products"* names a real product and a real relationship, and the corpus simply never records it. We decline all sixteen. An earlier build answered six of them with sentences like *"Fiona Brown, member of, ActionGenie"*: fluent, sourced, and wrong. Similarity search cannot decline at all.

**Where it is weak, and why.** Two distinct causes, neither fixable by prompt tuning:

1. **Most HERB answerable questions are aggregate or superlative**, not relational: *"the engineer with the **highest number** of approved PRs"*, *"engineers who resolved the **maximum number** of customer bugs"*, *"who worked on the **previous release**"*. Those need counting, ranking and release-window reasoning. Arbiter answers *"what is true about X, and who said so"*, which is a different query class. It declines them rather than guessing, which is right, and still scores zero against the benchmark, which is fair.
2. **Accuracy falls as corpus breadth grows.** Every product ships a "Market Research Report" and a "Product Vision Document", so title matching becomes ambiguous across products. Anchoring is filtered by the project a document belongs to, which is correct but insufficient, and the remaining loss is in retrieval breadth rather than in anchoring. This is the honest limitation of a keyword-and-fuzzy planner on a wide corpus, and it is the first thing worth fixing with more time.

The bug-resolution questions are a third, smaller case. HERB records that information only in Slack prose, and tier B is capped by design, so those claims never enter the graph. The abstention is correct given what was ingested, and is still counted as a miss here.

Answerable questions ask for *sets* ("the authors and key reviewers of the Market Research Report" has 11 ground-truth ids), so they are scored as retrieval rather than as a rendered sentence. Getting from 1% to 32% recall took three graph-level fixes, none of them prompt engineering:

- **HERB has no thread structure.** `ThreadReplies` is empty in all 1,084 messages, and a conversation is instead a run of top-level messages sharing a channel and date. Grouping them is what makes "who reviewed this document" reachable at all.
- **Documents are cited by URL**, not by field, as in `<https://…/docs/onforcex_market_research_report|Market Research Report>`, so citations become `REFERENCES` edges from every message in the citing conversation.
- **People are identifiers, not names.** Slack and documents use `eid_13fdff84`, GitHub PRs use `EMP_615921487`. There is no evidence linking the two schemes, so entity resolution keeps them apart (350 `eid_*` and 144 `EMP_*` in one product) rather than inventing a merge. The org chart in `salesforce_team.json` then makes `REPORTS_TO` deterministic: 295 claims, no LLM.

## Quickstart

Requires Docker and Python 3.10+.

```powershell
powershell -File scripts\hydradb_up.ps1      # MinIO + a HydraDB node
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
python data\seed\build_seed.py               # build the seed corpus
python -m ingest.load                        # parse -> resolve -> arbitrate -> write
python cli.py ask "who is ENG-4471 assigned to?"
```

No API key is needed for any of the above. Ingestion, entity resolution, arbitration, traversal and abstention are all deterministic. A key is only used for LLM extraction over free text and for rendering answers as prose.

To include free text (Slack threads, email, meeting transcripts) copy `.env.example` to `.env`, add a key, and rerun:

```powershell
python -m ingest.load --tier-b
```

Any OpenAI-compatible provider works (`LLM_PROVIDER=gemini|groq|cerebras|openrouter|openai|ollama|anthropic`). The default is **Gemini's free tier**, which is enough for this corpus. Extraction is cached on a content hash, so reruns cost nothing.

### The demo questions

```powershell
python cli.py ask "who is ENG-4471 assigned to?"                    # lookup + field-map provenance
python cli.py ask "what does @soham work on?"                       # alias-crossing multi-hop
python cli.py ask "who does @soham report to?"                      # fact stated only in email prose
python cli.py ask "who owns Atlas Migration?"                       # Slack vs transcript, arbitrated
python cli.py ask "who owns Atlas Migration?" --as-of 2026-03-15    # who owned it in March
python cli.py ask "when does Atlas Migration launch?"               # three sources, two superseded
python cli.py ask "what is the budget for Atlas Migration?"         # abstains, with a reason
python cli.py ask "who does Wei Chen report to?"                    # abstains: Sam's manager is not Wei's
python cli.py entities                                              # merge evidence per person
```

The last one matters more than it looks. Wei sits one hop from Sam, whose reporting line *is* recorded, and that adjacency is exactly what makes a retrieval system answer confidently about the wrong person.

## How HydraDB is used, and what breaks without it

Every answer is a traversal over provenance edges in HydraDB. The graph holds canonical entities, their aliases, every source document, and one `Claim` node per extracted statement, wired together with typed edges.

```
(:Claim {predicate, asserted_at, authority, score, status})
   -[:ABOUT]->        (:Person|:Project|:Artifact…)   subject
   -[:OBJECT]->       (…)                              object
   -[:SOURCED_FROM]-> (:Artifact)                      the document it came from
   -[:ASSERTED_BY]->  (:Person)                        who said it
(:Claim)-[:SUPERSEDES]->(:Claim)      (:Alias)-[:ALIAS_OF]->(:Person)
```

Reads use HydraDB's `algo.SPpaths` for materialized traversal paths, and bounded variable-length patterns for expansion. **Remove the graph and the system loses multi-hop across tools, supersession chains, as-of queries, and the coverage test that abstention depends on.** It degrades into exactly the vector RAG it is meant to beat.

The engine implements a deliberately narrow openCypher subset. Nine query forms are rejected outright, and three of those constraints shaped the data model, each pushing it toward being *more* graph-native:

| Constraint | Consequence |
|---|---|
| No list-valued properties | Aliases and ACL groups became nodes and edges, so alias merges are **visible in the retrieved subgraph** instead of hidden in a field |
| Batched edges take one type and no properties | All provenance lives on the `Claim` node, and status is encoded in the relationship type (`SCHEDULED_FOR` vs `SCHEDULED_FOR_SUPERSEDED`), keeping traversal filterable |
| Node ids must be non-negative integers | Keys are hashed (`blake2b(key)[:8] >> 1`), with the readable key kept as a property |

Other rejected forms include `MERGE` on relationships, `CREATE … RETURN`, untyped relationship patterns, `count(n)` (only `count(*)` works), functions in `RETURN`, and bare `MATCH (n)`. `python scripts/spike_hydra.py` exercises every supported operation and then asserts each rejected form still fails with the engine's own message, so the constraints stay verifiable rather than becoming folklore.

Two deployment notes, both found the hard way:

- `CLOUD_PROVIDER=local` accepts a few writes and then fails every mutation, because `LocalFileSystem` does not implement the conditional puts SlateDB needs for the writer lease. `scripts/hydradb_up.ps1` therefore runs MinIO as the object store.
- There is no `DELETE`, so reloading over an existing graph would duplicate every edge. `scripts/reset_graph.ps1` empties the bucket instead, and verifies the wipe before reporting success.

## How it works

**Write path:** parse, extract, resolve, arbitrate, write.

- **Tier A (deterministic, whole corpus).** One generic parser driven by `ontology/schema.yaml`. Per-source field maps turn `jira.fields.assignee`, `linear.assignee.name` and `hubspot.owner` into canonical predicates with no LLM involved. Adding a tenth tool is a YAML edit.
- **Tier B (LLM, free text only).** The predicate vocabulary is compiled into a JSON Schema `enum` and enforced by the API, so the extractor *structurally cannot* invent a predicate. On providers offering only loose JSON mode, the same check runs client-side and violations are dropped. Anything that genuinely does not fit becomes `UNMAPPED` with the raw phrase retained. Cached by content hash.
- **Alignment (`resolve/align.py`).** Prose does not speak in canonical keys, and this is where most of the ontology work actually happens. Four passes, each conservative and each schema-driven:
  - *surface*: strip determiners, then align to a known entity only on strong evidence, so `the Atlas migration` becomes `Atlas Migration`. A structured field outranks the extractor's type guess, but only on exact containment, so `Atlas launch` retypes to the Linear ticket rather than minting a project.
  - *orientation*: flip claims whose types violate domain and range but satisfy them reversed.
  - *same relation, different word*: `remap` in the schema. "Atlas is assigned to Sam" said of a **project** means ownership, said of a **ticket** it means assignment.
  - *functional form*: rewrite a multi-valued predicate to its functional inverse (`OWNS` becomes `OWNED_BY`) so competing claims land in one arbitrable set.

  Without these, the two halves of a real contradiction, Slack's "I'm taking the Atlas migration" and a transcript's "it moved to me in Jira", sit under different predicates on different nodes and never meet.
- **Entity resolution.** Normalize, block, score, **veto**, union-find. Three vetoes are absolute: conflicting emails, conflicting surnames, and *addressed-by-name*, since an author who writes "Thanks Priya" is not Priya. Every merge stores its evidence, which the CLI displays.
- **Arbitration.** A published, tunable formula, `0.35*authority + 0.30*recency + 0.15*specificity + 0.15*corroboration - 0.05*hedging`, with a source-authority table (Jira 1.00 down to Slack 0.50). Losing claims are **never deleted**. They are marked superseded and linked with `:SUPERSEDES`. Conflicts are only detected for *functional* predicates, because two tickets assigned to one person is not a contradiction.

**Read path:** plan, gate, traverse, arbitrate, generate, verify.

The planner is deterministic, so abstention tests something real rather than the model's willingness to wander. Three gates run **before** any model call:

| Gate | Test | Example message |
|---|---|---|
| 1. Entity | Do the entities exist? | *Not in the data: no record of "project zephyr" in the corpus.* |
| 2. Coverage | Is the asked-about predicate present? | *Found atlas migration, but no budget is recorded. Recorded: customer relationship, discussion, scheduled date, work.* |
| 3. Sufficiency | Is the best claim strong enough? | *Only an unconfirmed mention in slack (score 0.31 < 0.35).* |

Two further refusals sit alongside them: a question that maps to no predicate in the vocabulary is declined outright, and a question naming things the corpus never records is declined even when its entity and predicate both resolve. That second rule is what stops "team members who shared competitor demos" from being answered with a team roster.

After generation, every cited claim id is checked against what was actually retrieved. An invented citation discards the generated text and falls back to the deterministic rendering.

## Repo layout

```
ontology/schema.yaml     node types, predicate vocabulary (cardinality, temporality, inverse, remap), source field maps
graph/                   HydraDB adapter + domain models
llm.py                   provider-agnostic LLM client (gemini/groq/cerebras/openrouter/openai/ollama/anthropic)
ingest/                  parsers (tier A), LLM extraction (tier B), HERB reader, graph assembly and writing
resolve/                 entity resolution (normalize, features, engine) + claim alignment
arbiter/                 conflict policy and supersession
answer/                  query planner, traversal, gates, grounded generation, citation verification
eval/                    28-question seed suite + similarity baseline + HERB scorer
cli.py                   the interface
data/seed/               synthetic 9-tool corpus with planted ground truth
scripts/                 stack up, graph reset, capability spike, resolution report, scale test
```

## Scale

`python scripts/scale_test.py --docs 39000 --write`, on a corpus shaped like HERB:

| docs | claims | nodes | edges | CPU | write | disk |
|---:|---:|---:|---:|---:|---:|---:|
| 9,000 | 20,000 | 31,120 | 107,163 | 10.3 s | 81 s | 214 MB |
| **38,997** | **86,660** | **127,886** | **456,034** | **16.9 s** | **574 s** | **1,078 MB** |

Full-corpus scale is not a projection. Parse, resolve, arbitrate and assemble take **under 20 seconds** for 39k documents, and the graph writes in about ten minutes at roughly 1,000 elements per second with no query timeouts. Entity resolution grows *sub*-linearly as the employee pool saturates, because blocking keeps comparisons inside buckets.

## Scope and honesty

- **Two corpora, deliberately.** The seed corpus is synthetic: 28 documents across 9 tools in native record shapes, with planted alias clusters, contradictions, near-duplicates, gaps, and an over-merge trap (two different people named Priya in one thread). It exists so entity resolution, arbitration and abstention can be measured against ground truth *we control*, because HERB's questions cannot test a trap nobody planted. HERB is the check that none of it was overfitted to our own data. Both are reported above, including where HERB goes badly.
- **HERB results cover 5 products of 30.** Loading more is a flag (`--products N`), and the scale test shows the full corpus fits, but every number above comes from what was actually run rather than extrapolated.
- **Tiered ingestion is a deliberate cost decision**, documented rather than hidden. Deterministic parsing runs over every document, and LLM extraction runs over free text only, capped by `TIER_B_DOC_LIMIT`.
- **Model choice is left to the operator.** `llm.py` talks to any OpenAI-compatible endpoint, so the cost and quality trade sits in `.env` rather than being made silently in code.

## License

MIT, see [LICENSE](LICENSE). HydraDB itself is AGPL-3.0 and is used unmodified, over the network, as a separate service.

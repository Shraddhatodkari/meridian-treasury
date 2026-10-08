# How the author's coursework is applied in this project

| Certification | Where it is used in the code |
|---|---|
| **Building with the Claude API** (Anthropic) | `llm/claude.py`: native Messages API, forced tool use for schema-valid structured output, prompt caching (`cache_control`), per-run budgets, and the same client against the direct API, Vertex AI or Bedrock |
| **Building LLM Applications with Prompt Engineering** (NVIDIA) | `agents/prompts.py` and the system prompt: role prompts, output schemas, grounding rules. `evaluation/benchmark.py`: labelled planner set as a prompt regression test |
| **Introduction to Transformer-Based NLP** (NVIDIA) | `nlp/embeddings.py`: sentence-transformer (MiniLM) encoder for semantic retrieval over policies and contracts, plus a hybrid with BM25 |
| **Disaster Risk Monitoring Using Satellite Imagery** (NVIDIA) | `market/connectors.py` (live GDACS feed), `risk/events.py`, `analytics/scenarios.py` and `analytics/relationships.py`: hazard alerts mapped to entities and counterparties (hazard-exposure paths) and to cash-flow stress |
| **MongoDB** certification | `store/base.py`: MongoDB Atlas document model, unique indexes for a concurrency-safe audit chain, GridFS model registry, `$vectorSearch` aggregation. `deploy/atlas/vector_index.json`. Tested against a real MongoDB 8.0 server |
| **Startup School: Prompt to Prototype** (Google for Startups) | `docs/product-brief.md`: users, jobs-to-be-done, MVP scope, success metrics |
| **Mastercard — Advisors & Consulting Services job simulation** | `docs/business-case.md`: situation–complication–resolution, value drivers, roadmap, risks. `analytics/insights.py`: the CFO-question framework, where each answer has a headline, evidence, confidence and limitations |
| Generative AI with Diffusion Models (NVIDIA) | Not used. Image generation does not fit a treasury problem |

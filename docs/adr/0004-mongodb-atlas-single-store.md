# ADR-0004: MongoDB Atlas for documents, runs, audit and vectors

**Status:** Accepted

**Context.** The platform needs flexible financial documents (invoices, entities), run and approval state,
an audit log and semantic search over policies and contracts.

**Decision.** Use one MongoDB Atlas cluster with Atlas Vector Search (`$vectorSearch`) for policy chunks.
Hybrid retrieval fuses vector and BM25 rankings with Reciprocal Rank Fusion. A store interface keeps an
in-memory implementation for tests, and mongomock-backed tests cover the Mongo code path.

**Consequences.** One database to secure, back up and audit. The vector index definition is versioned in `deploy/atlas/`.

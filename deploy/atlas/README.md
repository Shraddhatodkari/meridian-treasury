# MongoDB Atlas setup

1. Create a cluster (the free M0 tier is enough for the demo) and a database user. Allow your app's egress IPs.
2. `export MERIDIAN_MONGODB_URI="mongodb+srv://USER:PASS@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority"`
3. Start the API once. It seeds the collections (`entities`, `accounts`, `receivables`, `payables`, `recurring`,
   `risk_events`, `meta`) and indexes the policy documents into `policy_chunks` with 384-d embeddings.
4. In Atlas → *Search & Vector Search* → *Create index* → JSON editor → paste `vector_index.json`
   (database `meridian`, collection `policy_chunks`).
5. When the URI contains `mongodb.net`, Meridian uses `$vectorSearch` automatically. Otherwise it runs exact
   cosine search client-side (self-hosted MongoDB, mongomock in tests).

Collections `runs` (agent runs, proposed actions, approvals) and `audit` (hash-chained ledger) are written at runtime.

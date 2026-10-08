"""Policy & contract retrieval (RAG) with clause-level citations.

Documents are split at clause headings ("## TP-2.1 ..."), so every retrieved chunk carries a
citable clause ID. Each chunk is scanned for prompt-injection at INGESTION time; tainted
chunks are stored but quarantined (never returned to an LLM), and the event is reported.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from meridian.config import PACKAGE_ROOT
from meridian.governance.guardrails import scan_text
from meridian.nlp.embeddings import get_embedder

DOC_DIR = PACKAGE_ROOT / "data" / "documents"
_CLAUSE = re.compile(r"^## (?P<id>[A-Z0-9]+-[\d.]+)\s+(?P<title>.+)$", re.M)
_META = re.compile(r"<!-- meta:(?P<body>.*?)-->")
MIN_RELEVANCE = 0.08   # below this (and with no keyword match) a hit is reported as not relevant


def doc_meta(text: str) -> dict:
    m = _META.search(text)
    out = {}
    if m:
        for part in m["body"].split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def chunk_document(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    title = text.splitlines()[0].lstrip("# ").strip()
    meta = doc_meta(text)
    sha = hashlib.sha256(text.encode()).hexdigest()[:16]
    matches = list(_CLAUSE.finditer(text))
    chunks = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        chunks.append({"chunk_id": f"{path.stem}:{m['id']}", "clause_id": m["id"], "clause_title": m["title"],
                       "document": title, "source": path.name, "text": body,
                       "doc_version": meta.get("version"), "effective": meta.get("effective"),
                       "owner": meta.get("owner"), "doc_sha256": sha})
    return chunks


def index_documents(store, doc_dir: Path = DOC_DIR) -> dict:
    emb = get_embedder()
    chunks = [c for p in sorted(doc_dir.glob("*.md")) for c in chunk_document(p)]
    vecs = emb.embed([f"{c['clause_title']}. {c['text']}" for c in chunks])
    quarantined = []
    for c, v in zip(chunks, vecs, strict=True):
        hits = scan_text(c["text"])
        c["quarantined"] = bool(hits)
        c["embedding"] = [float(x) for x in v]
        c["embedder"] = emb.name
        if hits:
            quarantined.append({"chunk_id": c["chunk_id"], "patterns": hits})
    store.upsert_chunks(chunks)
    return {"chunks": len(chunks), "quarantined": quarantined, "embedder": emb.name}


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "be", "for", "on", "by", "can", "we",
         "our", "what", "who", "how", "much", "does", "do", "may", "with", "any", "out", "at"}


def _terms(text: str) -> list[str]:
    return [t[:6] for t in _TOKEN.findall(text.lower()) if t not in _STOP]  # crude stemming via prefix


def _bm25(query: str, docs: list[dict], k1: float = 1.4, b: float = 0.75) -> list[float]:
    import math

    toks = [_terms(f"{d['clause_title']} {d['document']} {d['text']}") for d in docs]
    avg = sum(map(len, toks)) / max(len(toks), 1)
    q = set(_terms(query))
    scores = []
    for t in toks:
        s = 0.0
        for term in q:
            df = sum(1 for x in toks if term in x)
            if not df:
                continue
            idf = math.log(1 + (len(toks) - df + 0.5) / (df + 0.5))
            tf = t.count(term)
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(t) / avg))
        scores.append(s)
    return scores


def retrieve(store, query: str, k: int = 4) -> list[dict]:
    """Hybrid retrieval: semantic vector search + BM25 keyword scoring, fused with Reciprocal Rank
    Fusion (the same pattern as Atlas $vectorSearch + $search hybrid queries)."""
    vec = get_embedder().embed([query])[0]
    cands = [h for h in store.vector_search([float(x) for x in vec], k=200) if not h.get("quarantined")]
    if not cands:
        return []
    vec_rank = {c["chunk_id"]: i for i, c in enumerate(cands)}
    kw = _bm25(query, cands)
    kw_rank = {c["chunk_id"]: i for i, (c, _) in enumerate(sorted(zip(cands, kw, strict=True), key=lambda x: -x[1]))}
    fused = sorted(cands, key=lambda c: -(1 / (60 + vec_rank[c["chunk_id"]]) + 1 / (60 + kw_rank[c["chunk_id"]])))
    kw_score = {c["chunk_id"]: s for c, s in zip(cands, kw, strict=True)}
    top_kw = max(kw) or 1.0
    now = datetime.now(UTC).isoformat()
    out = []
    for i, c in enumerate(fused[:k]):
        rel = round(0.5 * max(0.0, float(c.get("score", 0.0))) + 0.5 * kw_score[c["chunk_id"]] / top_kw, 3)
        out.append({**c, "rrf_rank": i + 1, "relevance": rel, "keyword_score": round(kw_score[c["chunk_id"]], 3),
                    "relevant": rel >= MIN_RELEVANCE and kw_score[c["chunk_id"]] > 0,
                    "confidence": "HIGH" if rel >= 0.5 else "MEDIUM" if rel >= 0.25 else "LOW",
                    "retrieved_at": now, "citation": f"{c['clause_id']} — {c['document']} (v{c.get('doc_version')})"})
    return out


def verify_citations(text: str, retrieved: list[dict]) -> dict:
    """Unsupported-claim check: every clause id cited in generated text must be among the retrieved,
    non-quarantined chunks (or the active policy-as-code)."""
    cited = set(re.findall(r"\b([A-Z]{2,4}\d?-\d+(?:\.\d+)?)\b", text))
    known = {h["clause_id"] for h in retrieved}
    unsupported = sorted(c for c in cited if c not in known)
    return {"cited": sorted(cited), "unsupported": unsupported, "verified": not unsupported}


def quarantined_chunks(store) -> list[dict]:
    vec = get_embedder().embed(["supplier note"])[0]
    return [h for h in store.vector_search([float(x) for x in vec], k=100) if h.get("quarantined")]

"""Text embeddings for policy retrieval.

* TransformerEmbedder — sentence-transformers (MiniLM, 384-d), a Transformer encoder producing
  semantic sentence embeddings. Install with `pip install -e ".[nlp]"`.
* HashingEmbedder     — dependency-free lexical fallback (also 384-d) so tests/CI run offline.
Both produce 384-dimensional L2-normalised vectors, matching the Atlas Vector Search index
definition in deploy/atlas/vector_index.json.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

DIMS = 384


class HashingEmbedder:
    name = "hashing-384"

    def __init__(self):
        self._v = HashingVectorizer(n_features=DIMS, ngram_range=(1, 1), alternate_sign=False, norm="l2",
                                    stop_words="english")

    def embed(self, texts: list[str]) -> np.ndarray:
        return self._v.transform(texts).toarray()


class TransformerEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self._m = SentenceTransformer(model_name)
        self.name = model_name

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._m.encode(texts, normalize_embeddings=True))


@lru_cache
def get_embedder():
    from meridian.config import get_settings

    s = get_settings()
    if s.embedder == "transformer":
        return TransformerEmbedder(s.transformer_model)
    return HashingEmbedder()

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from app.memory.vectors.embeddings.contracts import EmbeddingProvider
from app.memory.settings import MemorySettings

logger = logging.getLogger(__name__)


class EmbeddingService(EmbeddingProvider):
    """
    Local sentence-transformers embedding provider.

    Synchronous inference runs in a worker thread and does not block asyncio.
    Empty text is rejected; callers must omit it from vector indexing.
    """

    def __init__(self, settings: MemorySettings) -> None:
        self._model_path = settings.embedding_model_path
        self._expected_dimension = settings.vector_dimension

    async def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty(
                (0, self._expected_dimension),
                dtype=np.float32,
            )

        normalized_texts = [_require_non_empty_text(text) for text in texts]

        vectors = await asyncio.to_thread(
            _encode_sync,
            self._model_path,
            normalized_texts,
        )

        if vectors.ndim != 2:
            raise RuntimeError(
                f"Embedding model returned invalid shape: {vectors.shape}"
            )

        if vectors.shape[0] != len(normalized_texts):
            raise RuntimeError(
                "Embedding model returned unexpected vector count: "
                f"expected {len(normalized_texts)}, got {vectors.shape[0]}"
            )

        if vectors.shape[1] != self._expected_dimension:
            raise RuntimeError(
                "Embedding dimension does not match configuration: "
                f"expected {self._expected_dimension}, got {vectors.shape[1]}"
            )

        return vectors

    async def encode_one(self, text: str) -> np.ndarray:
        vectors = await self.encode([text])
        return vectors[0]


@lru_cache(maxsize=4)
def _get_model(model_path: str) -> SentenceTransformer:
    logger.info("Loading embedding model: %s", model_path)
    return SentenceTransformer(model_path)


def _encode_sync(
    model_path: str,
    texts: Sequence[str],
) -> np.ndarray:
    model = _get_model(model_path)

    vectors = model.encode(
        list(texts),
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    return np.asarray(vectors, dtype=np.float32)


def _require_non_empty_text(value: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError("Cannot create embedding for empty text")

    return normalized
from __future__ import annotations

from collections.abc import Sequence

import numpy as np


class DeterministicEmbeddingProvider:
    """
    Fast local fake provider for tests.

    Vectors are deterministic from text and non-zero for non-empty text.
    They are not semantic embeddings and must never be used in production.
    """

    def __init__(self, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")

        self._dimension = dimension
        self.calls: list[list[str]] = []

    async def encode(self, texts: Sequence[str]) -> np.ndarray:
        normalized = [self._require_text(text) for text in texts]
        self.calls.append(normalized)

        return np.stack(
            [self._vector_for(text) for text in normalized],
            axis=0,
        ) if normalized else np.empty(
            (0, self._dimension),
            dtype=np.float32,
        )

    async def encode_one(self, text: str) -> np.ndarray:
        vectors = await self.encode([text])
        return vectors[0]

    def _vector_for(self, text: str) -> np.ndarray:
        vector = np.zeros(self._dimension, dtype=np.float32)

        for index, byte in enumerate(text.encode("utf-8")):
            vector[index % self._dimension] += (byte % 31) + 1

        norm = np.linalg.norm(vector)
        if norm == 0:
            raise ValueError("Cannot create a zero fake embedding")

        return vector / norm

    @staticmethod
    def _require_text(text: str) -> str:
        normalized = text.strip()

        if not normalized:
            raise ValueError("Cannot create embedding for empty text")

        return normalized
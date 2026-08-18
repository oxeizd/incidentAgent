from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class EmbeddingProvider(Protocol):
    """Async provider of normalized float32 text embeddings."""

    async def encode(self, texts: Sequence[str]) -> np.ndarray: ...

    async def encode_one(self, text: str) -> np.ndarray: ...
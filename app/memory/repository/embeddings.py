import asyncio
import logging
import numpy as np
from typing import Sequence
from functools import lru_cache
from app.config import memory_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    logger.info("Loading embedding model: %s", memory_settings.embeddings_model_path)
    return SentenceTransformer(memory_settings.embeddings_model_path)


def _encode_sync(texts: Sequence[str]) -> np.ndarray:
    model = _get_model()
    dim = memory_settings.vector_dim
    results: list[np.ndarray] = []
    for text in texts:
        if text:
            vec = model.encode(text, normalize_embeddings=True).astype(np.float32)
        else:
            vec = np.zeros(dim, dtype=np.float32)
        results.append(vec)
    return np.stack(results)


async def encode(texts: Sequence[str]) -> np.ndarray:
    """ИСПРАВЛЕНО: раньше синхронный вызов блокировал event loop."""
    return await asyncio.to_thread(_encode_sync, texts)


async def encode_one(text: str) -> np.ndarray:
    result = await encode([text])
    return result[0]
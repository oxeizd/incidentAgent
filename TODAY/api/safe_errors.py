from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class FailureCode(StrEnum):
    INPUT_INVALID = "input_invalid"
    DATA_UNAVAILABLE = "data_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    INTERRUPT_STALE = "interrupt_stale"
    INTERNAL = "internal"


@dataclass(frozen=True)
class SafeFailure:
    code: FailureCode
    message: str
    retryable: bool = False
    retry_after_ms: int | None = None


def user_fallback_for_exception(exc: Exception) -> SafeFailure:
    logger.exception("Conversation run failed: %s", exc)
    return SafeFailure(
        code=FailureCode.INTERNAL,
        message="Не удалось завершить задачу. Попробуйте ещё раз.",
        retryable=True,
        retry_after_ms=1000,
    )

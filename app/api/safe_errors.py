from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class FailureCode(StrEnum):
    INPUT_INVALID = "input_invalid"
    DATA_UNAVAILABLE = "data_unavailable"
    TOOL_UNAVAILABLE = "tool_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    TIMEOUT = "timeout"
    INTERRUPT_STALE = "interrupt_stale"
    INTERNAL = "internal"


@dataclass(frozen=True)
class SafeFailure:
    code: FailureCode
    message: str
    retryable: bool = False
    retry_after_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "retry_after_ms": self.retry_after_ms,
        }


def user_fallback_for_exception(exc: Exception, *, stage: str | None = None) -> SafeFailure:
    """Log technical details server-side; return only a user-safe stable contract."""
    logger.exception("Agent failure at stage=%s: %s", stage, exc)
    normalized_stage = (stage or "").lower()

    if "search" in normalized_stage:
        return SafeFailure(
            FailureCode.TOOL_UNAVAILABLE,
            "Не удалось получить данные поиска. Попробуйте ещё раз чуть позже или уточните запрос.",
            retryable=True,
            retry_after_ms=1000,
        )
    if normalized_stage in {"rca", "rca_gate", "analyzer"}:
        return SafeFailure(
            FailureCode.DATA_UNAVAILABLE,
            "Не удалось завершить анализ по текущим данным. Уточните факты, логи или метрики и попробуйте снова.",
        )
    if normalized_stage in {"creator", "presentation", "build_presentation"}:
        return SafeFailure(
            FailureCode.TOOL_UNAVAILABLE,
            "Не удалось завершить подготовку презентации. Исходные данные сохранены — попробуйте создать её ещё раз.",
            retryable=True,
            retry_after_ms=1000,
        )
    if normalized_stage in {"editor", "apply_edit"}:
        return SafeFailure(
            FailureCode.INTERNAL,
            "Не удалось применить правку. Исходный отчёт не изменён.",
        )
    return SafeFailure(
        FailureCode.INTERNAL,
        "Не удалось завершить задачу. Попробуйте ещё раз.",
        retryable=True,
        retry_after_ms=1000,
    )
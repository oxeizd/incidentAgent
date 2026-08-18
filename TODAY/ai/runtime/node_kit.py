from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import BaseModel

from app.ai.graph.interrupts import ask_user
from app.ai.runtime.form_schema import build_fields_schema
from app.ai.runtime.typed_worker import TypedWorkerState, get_typed
from app.ai.schemas.worker import WorkerState
from app.services.reasoning import emit_reasoning


logger = logging.getLogger(__name__)

NodeFunction = Callable[["NodeContext"], Awaitable[dict]]

_CONFIRM_TRUE = frozenset(
    {
        "да",
        "yes",
        "true",
        "ok",
        "ок",
        "confirm",
        "подтверждаю",
        "подтвердить",
    }
)

_CONFIRM_FALSE = frozenset(
    {
        "нет",
        "no",
        "false",
        "cancel",
        "отмена",
        "не надо",
        "не подтверждаю",
    }
)


class UserDeviated(Exception):
    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text
        super().__init__(raw_text)


@dataclass(slots=True)
class NodeContext:
    worker: WorkerState
    typed: TypedWorkerState
    node_name: str

    @classmethod
    def from_worker(
        cls,
        worker: WorkerState,
        node_name: str,
    ) -> "NodeContext":
        return cls(
            worker=worker,
            typed=get_typed(worker),
            node_name=node_name,
        )

    def log(
        self,
        message: str,
        *,
        stage: str | None = None,
        **extra: Any,
    ) -> None:
        emit_reasoning(
            node=self.node_name,
            message=message,
            worker_id=self.worker["worker_id"],
            stage=stage or self.node_name,
            **extra,
        )

    async def ask(
        self,
        question: str,
        *,
        interaction_type: str = "question",
        options: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        return interrupt(
            ask_user(
                question=question,
                worker_id=self.worker["worker_id"],
                interaction_type=interaction_type,
                round_number=self.worker["rounds"] + 1,
                options=options,
                metadata=metadata,
            )
        )

    async def confirm(
        self,
        question: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        raw = await self.ask(
            question,
            interaction_type="confirmation",
            metadata=metadata,
        )

        return self._normalize_confirmation(raw)

    async def ask_form(
        self,
        question: str,
        schema: type[BaseModel],
        *,
        current: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Structured interrupt form.

        current передаётся как prefilled values. Если клиент не поддерживает
        форму и прислал строку, creator получит `_raw_text_fallback`.
        """
        base = dict(current or {})
        fields = build_fields_schema(
            schema,
            current=base,
        )

        raw = interrupt(
            ask_user(
                question=question,
                worker_id=self.worker["worker_id"],
                interaction_type="form",
                round_number=self.worker["rounds"] + 1,
                fields=fields,
                metadata=metadata,
            )
        )

        if isinstance(raw, dict):
            merged = dict(base)
            merged.update(
                {
                    key: value
                    for key, value in raw.items()
                    if value not in (None, "")
                }
            )
            return merged

        return {
            **base,
            "_raw_text_fallback": str(raw),
        }

    def update(
        self,
        *,
        payload_update: dict[str, Any] | None = None,
    ) -> dict:
        self._apply_payload_update(payload_update)

        return {
            "payload": self.worker["payload"],
        }

    def running(
        self,
        *,
        message: str,
        payload_update: dict[str, Any] | None = None,
    ) -> dict:
        self._apply_payload_update(payload_update)

        return {
            "payload": self.worker["payload"],
            "rounds": self.worker["rounds"] + 1,
            "status": "running",
            "history": [AIMessage(content=message)],
        }

    def awaiting(
        self,
        *,
        question: str,
        payload_update: dict[str, Any] | None = None,
    ) -> dict:
        self._apply_payload_update(payload_update)

        return {
            "payload": self.worker["payload"],
            "rounds": self.worker["rounds"] + 1,
            "status": "awaiting_user_input",
            "history": [AIMessage(content=question)],
        }

    def done(
        self,
        *,
        message: str,
        summary: dict[str, Any] | None = None,
        payload_update: dict[str, Any] | None = None,
    ) -> dict:
        return self.finished(
            status="done",
            message=message,
            summary=summary,
            payload_update=payload_update,
        )

    def finished(
        self,
        *,
        status: str,
        message: str,
        summary: dict[str, Any] | None = None,
        payload_update: dict[str, Any] | None = None,
    ) -> dict:
        self._apply_payload_update(payload_update)

        return {
            "payload": self.worker["payload"],
            "status": status,
            "summary_for_parent": summary,
            "history": [AIMessage(content=message)],
        }

    def failed(
        self,
        *,
        code: str,
        message: str,
    ) -> dict:
        return {
            "status": "failed",
            "error": {
                "code": code,
                "message": message,
            },
            "history": [AIMessage(content=message)],
        }

    def cancelled(
        self,
        *,
        message: str,
    ) -> dict:
        return {
            "status": "cancelled",
            "history": [AIMessage(content=message)],
        }

    def _apply_payload_update(
        self,
        payload_update: dict[str, Any] | None,
    ) -> None:
        if payload_update is None:
            return

        self.typed.payload = {
            **self.typed.payload.model_dump(),
            **payload_update,
        }

    @staticmethod
    def _normalize_confirmation(raw: Any) -> bool:
        if isinstance(raw, bool):
            return raw

        if isinstance(raw, dict):
            return bool(raw.get("confirmed", False))

        if isinstance(raw, str):
            normalized = raw.strip().lower()

            if normalized in _CONFIRM_TRUE:
                return True

            if normalized in _CONFIRM_FALSE:
                return False

        return False


def worker_node(
    node_name: str,
) -> Callable[
    [NodeFunction],
    Callable[[WorkerState], Awaitable[dict]],
]:
    def decorate(
        function: NodeFunction,
    ) -> Callable[[WorkerState], Awaitable[dict]]:
        async def wrapped(worker: WorkerState) -> dict:
            context = NodeContext.from_worker(
                worker,
                node_name,
            )

            try:
                return await function(context)
            except GraphInterrupt:
                raise
            except UserDeviated as exc:
                logger.info(
                    "Worker %s deviated from pending question: %r",
                    worker["worker_id"],
                    exc.raw_text,
                )

                return {
                    "status": "deviated",
                    "error": {
                        "code": "user_deviated",
                        "message": exc.raw_text,
                    },
                }
            except Exception:
                logger.exception(
                    "AI node failed: node=%s worker_id=%s",
                    node_name,
                    worker["worker_id"],
                )

                return context.failed(
                    code="node_error",
                    message=(
                        f"Внутренняя ошибка во время шага "
                        f"«{node_name}»."
                    ),
                )

        wrapped.__name__ = getattr(
            function,
            "__name__",
            node_name,
        )
        wrapped.__doc__ = function.__doc__
        return wrapped

    return decorate
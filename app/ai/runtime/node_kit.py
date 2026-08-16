from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from langchain_core.messages import AIMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import BaseModel

from app.ai.graph.interrupts import ask_user
from app.ai.runtime.form_schema import build_fields_schema
from app.ai.runtime.typed_worker import TypedWorkerState, get_typed
from app.ai.schemas.worker import WorkerState
from app.services.llm import llm_client
from app.services.reasoning import emit_reasoning

logger = logging.getLogger(__name__)

NodeFn = Callable[["NodeCtx"], Awaitable[dict]]

_CONFIRM_POSITIVE = ("да", "yes", "true", "confirm", "подтверждаю", "ок", "ok", "конечно")
_CONFIRM_NEGATIVE = ("нет", "no", "false", "отмена", "не подтверждаю", "не надо", "неа")

_DEVIATION_CHECK_PROMPT = (
    "Пользователю задан уточняющий вопрос агентом (возможно, из нескольких "
    "частей). Определи: его новая реплика связана с этим вопросом (отвечает "
    "хотя бы на часть вопроса, даже кратко, неполно, с опечатками или не по "
    "всем пунктам), или он явно уходит в сторону (просит отменить, начинает "
    "не связанную новую задачу, пишет что-то совсем не по теме)?\n\n"
    "При любом сомнении выбирай 'answer' — ошибочно посчитать реплику "
    "отклонением намного дороже, чем ошибочно посчитать её ответом (в худшем случае агент переспросит ещё "
    "раз). 'deviation' — только если реплика ЯВНО не имеет отношения к "
    "вопросу: явная отмена, явно другая задача, случайный текст не по теме.\n\n"
    "Сначала одним предложением обясни решение, затем на отдельной "
    "последней строке выведи строго одно слово: answer или deviation."
)


class UserDeviated(Exception):
    """
    Поднимается, когда resume-значение — НЕ ответ на заданный вопрос, а уход
    в сторону. Ловится в worker_node: воркер завершается со status='deviated'
    и сырым текстом в error.message, а не продолжает исполнение как обычно.
    """

    def __init__(self, raw_text: str):
        self.raw_text = raw_text
        super().__init__(f"Пользователь отклонился от вопроса: {raw_text!r}")


async def check_not_deviation(question: str, raw_value: Any, *, options: Optional[list] = None) -> None:
    """
    Бросает UserDeviated, если raw_value — не ответ на question.

    Пропускает проверку (считает ответом без LLM-вызова), если:
      - raw_value НЕ строка (bool/dict от структурированного UI-компонента —
        такое не может быть "отклонением", это явный клик/выбор/форма);
      - raw_value точно совпадает с одним из предложенных options.
    """
    if not isinstance(raw_value, str):
        return
    if options and raw_value in options:
        return

    system = llm_client.build_system_message(
        role_instruction=_DEVIATION_CHECK_PROMPT,
        extra_context={"question": question, "user_reply": raw_value},
    )
    response = await llm_client.ainvoke([system], worker_kind="supervisor", max_tokens=80)

    content = response.content or ""
    lines = [line.strip().strip(".:").lower() for line in content.splitlines() if line.strip()]
    verdict = lines[-1] if lines else ""

    if verdict == "deviation":
        raise UserDeviated(raw_value)


@dataclass
class NodeCtx:
    """Контекст одного вызова ноды. Живёт ровно один вызов — не хранить между вызовами."""

    worker: WorkerState
    typed: TypedWorkerState
    node_name: str

    @classmethod
    def from_worker(cls, worker: WorkerState, node_name: str) -> "NodeCtx":
        return cls(worker=worker, typed=get_typed(worker), node_name=node_name)

    # ── наблюдаемость ──────────────────────────────

    def log(self, message: str, **extra: Any) -> None:
        emit_reasoning(
            self.node_name,
            message,
            worker_id=self.worker["worker_id"],
            stage=self.worker["kind"],
            visibility="user",
            **extra,
        )

    # ── диалог с пользователем ──────────────────────

    async def ask(self, question: str, *, type: str = "question", options: Optional[list] = None, **extra: Any) -> Any:
        """
        Обёртка над interrupt() + проверкой на отклонение. worker_id и round
        подставляются сами.

        ИСПРАВЛЕНО: параметр назывался `kind` — единственное расхождение с
        остальным контрактом проекта, где дискриминатор JSON-конверта везде
        называется `type` (см. app/ai/graph/interrupts.py). Переименован в
        `type` во всех вызывающих узлах (rca.py и т.п.).

        ВАЖНО: LangGraph при resume ПЕРЕИГрывает ноду с начала до этой точки.
        Любой побочный эффект перед ctx.ask() должен быть идемпотентным.
        """
        raw = interrupt(ask_user(
            question, self.worker["worker_id"], type=type,
            round=self.worker["rounds"] + 1, **({"options": options} if options else {}), **extra,
        ))
        await check_not_deviation(question, raw, options=options)
        return raw

    async def confirm(self, question: str, **extra: Any) -> bool:
        """
        Как ask(), но нормализует ответ к bool. Транспортный слой может
        прислать {"confirmed": true/false}, голый bool или текст "да"/"нет".

        type="confirmation" — это discriminator для клиента (да/нет-виджет
        вместо текстового поля), см. app/ai/graph/interrupts.py.

        ИСПРАВЛЕНО: раньше любой нераспознанный текстовый ответ (не входящий
        в список позитивных слов) молча трактовался как "нет" — пользователь,
        ответивший неоднозначно ("не уверен", "может быть"), тихо получал
        отказ от операции (например, отмену разрушающей правки в editor.py)
        без единого предупреждения. Теперь при неоднозначном ответе агент
        явно переспрашивает один раз, а не угадывает.
        """
        raw = await self.ask(question, type="confirmation", **extra)
        return await self._resolve_confirmation(question, raw, extra)

    async def _resolve_confirmation(self, question: str, raw: Any, extra: dict, *, _reasked: bool = False) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, dict):
            return bool(raw.get("confirmed", False))
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in _CONFIRM_POSITIVE:
                return True
            if normalized in _CONFIRM_NEGATIVE:
                return False
            if _reasked:
                # Второй неоднозначный ответ подряд — дальше не переспрашиваем
                # до бесконечности, трактуем как отказ (безопасный дефолт для
                # потенциально разрушающих операций), но это явное решение,
                # а не тихое совпадение с "любой непонятный текст = нет".
                return False
            clarified = await self.ask(
                f"Не понял ответ на «{question}» — подтверждаете? Ответьте «да» или «нет».",
                type="confirmation", **extra,
            )
            return await self._resolve_confirmation(question, clarified, extra, _reasked=True)
        return bool(raw)

    async def ask_form(
        self, question: str, schema: type[BaseModel], *, current: Optional[dict] = None, **extra: Any,
    ) -> dict[str, Any]:
        """
        Как ask(), но запрашивает не свободный текст, а структурированную
        форму: клиенту отдаётся JSON-описание полей (build_fields_schema) —
        какие обязательны, какие опциональны и что в них уже известно
        (value=None у пустых, заполненное значение у известных). См. исторически
        тот же подход в Vercel AI SDK (tool-approval UI) и AG-UI (generative UI):
        агент не диктует HTML, а отдаёт декларативную схему, а клиент
        сам решает, каким виджетом её показать.

        Ответ (resume) может быть:
          - dict {field_name: value}          — клиент поддерживает форму;
          - произвольная строка               — клиент без формы ответил
            текстом; в этом случае возвращается {**current, "_raw_text_fallback": raw},
            и вызывающая нода сама решает, как разобрать текст (см.
            app/ai/nodes/creator.py:collect_fields — LLM-фоллбек).

        current — уже известные значения (не "—"/None), используются и для
        построения fields-схемы, и как база для merge с ответом.
        """
        base = dict(current or {})
        fields_schema = build_fields_schema(schema, current=base)
        raw = interrupt(ask_user(
            question, self.worker["worker_id"], type="form",
            round=self.worker["rounds"] + 1, fields=fields_schema, **extra,
        ))

        if isinstance(raw, dict):
            await check_not_deviation(question, raw)
            merged = dict(base)
            merged.update({k: v for k, v in raw.items() if v not in (None, "")})
            return merged

        await check_not_deviation(question, raw)
        return {**base, "_raw_text_fallback": raw}

    # ── стандартные формы результата ноды ──────────────────

    def _apply_payload_update(self, payload_update: Optional[dict]) -> None:
        if payload_update:
            self.typed.payload = {**self.typed.payload.model_dump(), **payload_update}

    def update(self, *, payload_update: Optional[dict] = None) -> dict:
        self._apply_payload_update(payload_update)
        return {"payload": self.worker["payload"]}

    def finished(
        self, *, status: str, message: str, summary: Optional[dict] = None, payload_update: Optional[dict] = None,
    ) -> dict:
        self._apply_payload_update(payload_update)
        return {
            "payload": self.worker["payload"], "status": status,
            "summary_for_parent": summary, "history": [AIMessage(content=message)],
        }

    def done(self, *, message: str, summary: Optional[dict] = None, payload_update: Optional[dict] = None) -> dict:
        return self.finished(status="done", message=message, summary=summary, payload_update=payload_update)

    def running(self, *, message: str, payload_update: Optional[dict] = None) -> dict:
        self._apply_payload_update(payload_update)
        return {
            "payload": self.worker["payload"], "rounds": self.worker["rounds"] + 1,
            "status": "running", "history": [AIMessage(content=message)],
        }

    def awaiting(self, *, question: str, payload_update: Optional[dict] = None) -> dict:
        self._apply_payload_update(payload_update)
        return {
            "payload": self.worker["payload"], "rounds": self.worker["rounds"] + 1,
            "status": "awaiting_user_input", "history": [AIMessage(content=question)],
        }

    def failed(self, *, code: str, message: str) -> dict:
        return {
            "status": "failed", "error": {"code": code, "message": message},
            "history": [AIMessage(content=message)],
        }

    def cancelled(self, *, message: str) -> dict:
        return {"status": "cancelled", "history": [AIMessage(content=message)]}


def worker_node(node_name: str) -> Callable[[NodeFn], Callable[[WorkerState], Awaitable[dict]]]:
    """
    Превращает `async def fn(ctx: NodeCtx) -> dict` в узел графа.
    Сознательно НЕ используем functools.wraps — __wrapped__ ломает
    inspect.signature(), которым LangGraph определяет входную схему ноды.

    GraphInterrupt — пауза, не ошибка, пробрасывается дальше.
    UserDeviated — пользователь ушёл в сторону от вопроса, превращается в
    status="deviated" с сырым текстом в error.message.
    Всё остальное — реальный сбой, status="failed".
    """

    def decorator(fn: NodeFn) -> Callable[[WorkerState], Awaitable[dict]]:
        async def wrapper(worker: WorkerState) -> dict:
            ctx = NodeCtx.from_worker(worker, node_name)
            try:
                return await fn(ctx)
            except GraphInterrupt:
                raise
            except UserDeviated as dev:
                logger.info("Worker %s: пользователь отклонился от вопроса: %r", worker["worker_id"], dev.raw_text)
                return {"status": "deviated", "error": {"code": "user_deviated", "message": dev.raw_text}}
            except Exception as exc:
                logger.exception("Нода %r упала для worker_id=%s", node_name, worker["worker_id"])
                return ctx.failed(code="node_error", message=f"Внутренняя ошибка узла «{node_name}»: {exc}")

        wrapper.__name__ = getattr(fn, "__name__", node_name)
        wrapper.__doc__ = fn.__doc__
        wrapper.__qualname__ = getattr(fn, "__qualname__", wrapper.__name__)
        return wrapper

    return decorator

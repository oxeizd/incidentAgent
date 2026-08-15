from __future__ import annotations

import json
import logging
import ssl
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Type, TypeVar, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_gigachat import GigaChat
from pydantic import BaseModel, ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_TRANSIENT_MARKERS = ("timeout", "timed out", "connection", "503", "502", "429", "temporarily")
_MAX_STRUCTURED_RETRIES = 2


def _is_transient_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _llm_retry(fn):
    return retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=1, max=20),
        retry=retry_if_exception(_is_transient_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(fn)


class LLMClient:
    """
    Единая точка входа к GigaChat: обычный вызов (ainvoke), стриминг
    (astream), вызов с тулами (bind_tools), structured output
    (ainvoke_structured). Модель на вызов выбирается по worker_kind через
    settings.WORKER_MODEL_<KIND>.
    """

    def __init__(self) -> None:
        self._ssl_context = self._create_ssl_context()
        self._llm_cache: Dict[tuple, GigaChat] = {}

    # ── настройка соединения ────────────────────────────────────────

    def _create_ssl_context(self) -> Optional[ssl.SSLContext]:
        cert_paths = (settings.CA_CERT_PATH, settings.CLIENT_CERT_PATH, settings.CLIENT_KEY_PATH)
        if not all(cert_paths):
            logger.warning("SSL сертификаты не настроены — подключение к GigaChat без mTLS")
            return None

        ca_path, cert_path, key_path = (Path(p) for p in cert_paths)
        if not (ca_path.exists() and cert_path.exists() and key_path.exists()):
            logger.warning("Один из файлов сертификатов не найден на диске: %s", cert_paths)
            return None

        try:
            ctx = ssl.create_default_context(cafile=str(ca_path))
            ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
            return ctx
        except Exception:
            logger.exception("Не удалось создать SSL-контекст для GigaChat")
            return None

    def _get_llm(self, model: str, max_tokens: int, temperature: float) -> GigaChat:
        """Явный кэш инстансов GigaChat по (model, max_tokens, temperature)."""
        key = (model, max_tokens, temperature)
        if key not in self._llm_cache:
            self._llm_cache[key] = GigaChat(
                base_url=settings.GIGACHAT_BASE_URL,
                scope=settings.GIGACHAT_SCOPE,
                model=model,
                timeout=settings.GIGACHAT_TIMEOUT,
                temperature=temperature,
                max_tokens=max_tokens,
                verify_ssl_certs=self._ssl_context is not None,
                ssl_context=self._ssl_context,
            )
        return self._llm_cache[key]

    def _resolve_model(self, worker_kind: Optional[str], override: Optional[str]) -> str:
        if override:
            return override
        if worker_kind:
            per_kind_model = getattr(settings, f"WORKER_MODEL_{worker_kind.upper()}", None)
            if per_kind_model:
                return per_kind_model
        return settings.GIGACHAT_MODEL

    def _prepare(
        self, *, worker_kind: Optional[str], model: Optional[str],
        max_tokens: Optional[int], temperature: Optional[float],
    ) -> GigaChat:
        resolved_model = self._resolve_model(worker_kind, model)
        resolved_temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
        resolved_max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        return self._get_llm(model=resolved_model, max_tokens=resolved_max_tokens, temperature=resolved_temperature)

    @staticmethod
    def _build_response_format(schema: Union[Type[BaseModel], dict]) -> Dict[str, Any]:
        is_pydantic_model = isinstance(schema, type) and issubclass(schema, BaseModel)
        schema_payload = schema.model_json_schema() if is_pydantic_model else schema
        return {"type": "json_schema", "schema": schema_payload, "strict": True}

    @staticmethod
    def build_system_message(
        role_instruction: str, *, extra_context: Optional[dict] = None, output_contract: Optional[str] = None,
    ) -> SystemMessage:
        sections = [role_instruction.strip()]

        if extra_context:
            context_lines = "\n".join(f"- {key}: {value}" for key, value in extra_context.items())
            sections.append(f"Контекст:\n{context_lines}")

        if output_contract:
            sections.append(f"Формат ответа: {output_contract.strip()}")

        return SystemMessage(content="\n\n".join(sections))

    # ── основные вызовы ──────────────────────────────────────────────

    @_llm_retry
    async def ainvoke(
        self,
        messages: List[BaseMessage],
        *,
        worker_kind: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        response_format: Optional[Union[Type[BaseModel], dict]] = None,
        **kwargs: Any,
    ) -> AIMessage:
        llm = self._prepare(worker_kind=worker_kind, model=model, max_tokens=max_tokens, temperature=temperature)

        if response_format is not None:
            kwargs["response_format"] = self._build_response_format(response_format)

        try:
            return await llm.ainvoke(messages, **kwargs)
        except Exception:
            logger.exception("LLM ainvoke ошибка (worker_kind=%s)", worker_kind)
            raise

    async def astream(
        self,
        messages: List[BaseMessage],
        *,
        worker_kind: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[AIMessage, None]:
        llm = self._prepare(worker_kind=worker_kind, model=model, max_tokens=max_tokens, temperature=temperature)
        async for chunk in llm.astream(messages, **kwargs):
            yield chunk

    def bind_tools(
        self,
        tools: List[BaseTool],
        *,
        worker_kind: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ):
        llm = self._prepare(worker_kind=worker_kind, model=model, max_tokens=max_tokens, temperature=temperature)
        return llm.bind_tools(tools, **kwargs)

    # ── structured output ────────────────────────────────────────────

    async def ainvoke_structured(
        self,
        messages: List[BaseMessage],
        schema: Type[T],
        *,
        worker_kind: Optional[str] = None,
        max_retries: int = _MAX_STRUCTURED_RETRIES,
        **kwargs: Any,
    ) -> T:
        conversation = list(messages)
        last_error: Optional[Exception] = None

        for _ in range(max_retries + 1):
            response = await self.ainvoke(conversation, worker_kind=worker_kind, response_format=schema, **kwargs)
            raw_content = response.content
            try:
                data = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                conversation = [*conversation, HumanMessage(content=(
                    f"Твой предыдущий ответ не прошёл проверку схемы:\n{exc}\n\n"
                    "Верни JSON, строго соответствующий требуемой схеме, без пояснений вокруг."
                ))]

        assert last_error is not None
        raise last_error


llm_client = LLMClient()
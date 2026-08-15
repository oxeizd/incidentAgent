"""
app/api/agui.py

Мост между нашим LangGraph-графом (build_orchestrator_graph) и настоящим
AG-UI протоколом (docs.ag-ui.com) — тем самым, который, судя по всему,
ожидает плагин чата в Grafana ("ag ui transformer").

НЕ переизобретаем события AG-UI руками (RunStarted/TextMessageContent/
ToolCallStart/RunFinished с outcome=interrupt и т.п., см.
https://docs.ag-ui.com/concepts/events) — это отдельный, большой протокол
со стримингом токенов и аргументов по частям. Вместо этого используем
официальный адаптер ag-ui-langgraph, который берёт уже готовый
скомпилированный граф и сам транслирует его выполнение (включая
interrupt()/Command(resume=...), на которых построены
ctx.ask()/ctx.confirm()/ctx.ask_form() в app/ai/runtime/node_kit.py) в
поток AG-UI событий — без переписывания графа/registry/node_kit.

Установка:
    pip install ag-ui-langgraph

Это ДОБАСОнNy endpoint. Существующие /message, /threads/{id},
/api/v1/threads/* (app/api/app.py, app/api/schemas.py, app/api/sse.py) не
тронуты и продолжают обслуживать текущий фронтенд (index.html) через
свой лёгкий контракт с OpenAI-совместимыми tool_calls. AG-UI endpoint —
отдельный путь, для внешних потребителей (плагин чата Grafana и любой
другой клиент, ожидающий именно AG-UI).

ВАЖНО (честно про границы проверки): этот файл не проверен end-to-end —
запуск живого FastAPI-процесса, реальный вызов из Grafana-плагина или
из AG-UI Dojo — в среде, где это писалось, нет доступа ни к сети, ни к
установке пакетов. Синтаксис и структура вызова сверены с
документацией ag-ui-langgraph (PyPI) и официальной интеграцией
CopilotKit+LangGraph, но перед деплоем нужно:
  1. pip install ag-ui-langgraph
  2. uvicorn app.api.app:app --reload
  3. Открыть AG-UI Dojo (см. репозиторий ag-ui-protocol/ag-ui,
     integrations/langgraph/examples) и направить его на AGUI_PATH ниже —
     это эталонный клиент для ручной проверки протокола.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)

AGUI_PATH = "/agui"


def mount_agui_endpoint(app: FastAPI, graph) -> bool:
    """
    Монтирует AG-UI endpoint на уже скомпилированный orchestrator graph
    (app.ai.graph.orchestrator.build_orchestrator_graph). Вызывать после
    того, как граф собран (нужен checkpointer, доступный только внутри
    lifespan) — см. app/api/app.py:lifespan.

    Возвращает True, если endpoint примонтирован, False — если пакет
    ag-ui-langgraph не установлен. В последнем случае приложение не падает:
    остальные endpoint'ы (app/api/app.py) продолжают работать как раньше,
    просто AG-UI путь недоступен, пока пакет не поставят.
    """
    try:
        from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint
    except ImportError:
        logger.warning(
            "Пакет ag-ui-langgraph не установлен — AG-UI endpoint (%s) не примонтирован. "
            "Установите: pip install ag-ui-langgraph", AGUI_PATH,
        )
        return False

    agent = LangGraphAgent(
        name="incident_orchestrator",
        description=(
            "Мультиагентная система разбора инцидентов: поиск, RCA-анализ, "
            "редактирование отчёта, генерация презентации."
        ),
        graph=graph,
    )
    add_langgraph_fastapi_endpoint(app, agent, AGUI_PATH)
    logger.info("AG-UI endpoint примонтирован на %s", AGUI_PATH)
    return True

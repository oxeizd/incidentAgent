"""
app/ai/graph/orchestrator/context.py

Wave 5: единая точка dependency-injection для intent handlers. Handlers
получают только то, что реально нужно для technical execution (WorkerRunner,
скомпилированные subgraphs, classifier, artifact handlers, llm) — не
импортируют друг друга и не знают про весь orchestrator целиком.

`dispatcher` заполняется ПОСЛЕ конструирования (см.
app/ai/graph/orchestrator/graph.py:_get_context) — сознательно НЕ frozen:
app/ai/graph/orchestrator/deviation.py нужен доступ к диспетчеру, чтобы
реклассифицировать и повторно диспетчеризовать реплику после того, как
пользователь "отклонился" от вопроса воркера, а сам диспетчер строится ИЗ
handlers, которым уже нужен готовый context. Цикл разрывается присваиванием
атрибута после того, как оба объекта созданы, а не передачей context в
конструктор IntentDispatcher.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from app.ai.runtime.worker_runner import WorkerRunner
from app.ai.graph.orchestrator.classifier import IntentClassifier
from app.ai.graph.orchestrator.artifacts.registry import ArtifactHandlerRegistry


@dataclass
class OrchestratorContext:
    runner: WorkerRunner
    subgraphs: Mapping[str, Any]
    classifier: IntentClassifier
    artifact_handlers: ArtifactHandlerRegistry
    llm: Any
    dispatcher: Optional[Any] = None  # app.ai.graph.orchestrator.dispatcher.IntentDispatcher

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

from app.ai.schemas.conversation import (
    ArtifactRef,
    ExecutionPlan,
    PlannerDecision,
)
from app.ai.schemas.conversation_state import ConversationState
from app.services.llm import llm_client


PLANNER_PROMPT = """
Ты строишь план выполнения для IT-ассистента, который работает с инцидентами.

Верни только JSON, соответствующий PlannerDecision.

Есть два результата:

1. kind="chat"
   Используй только для приветствия, благодарности, справочного вопроса,
   короткого разговора или неясного текста, который не требует работы с
   инцидентами. Заполни chat_response готовым ответом пользователю.

2. kind="execute"
   Используй, когда нужно искать инциденты, делать RCA или создавать
   презентацию. Заполни plan полным ExecutionPlan.

Доступны только шаги:

- search: ищет инциденты и поручения. Может считать статистику и группировки
  обычными SQL/Python-операциями внутри search. Для статистики передай в
  inputs поле aggregations.
- rca: проводит RCA по найденному инциденту или переданному artifact ref.
- presentation: создаёт презентацию по результатам поиска и/или RCA.

Правила плана:

- Каждый шаг имеет уникальный step_id.
- depends_on содержит только step_id других шагов этого же плана.
- Независимые работы не связывай зависимостями: executor запустит их
  параллельно.
- RCA, которому нужны данные инцидента, зависит от search шага, который эти
  данные получает.
- Presentation зависит от шагов, чьи результаты использует.
- Для существующего результата используй только artifact ref из
  available_artifacts. Никогда не выдумывай ID или ссылки.
- Не создавай отдельный шаг для подсчётов, топов, группировок и сортировки:
  это inputs шага search.
- Не создавай edit шаг. Исправление или дополнение RCA выполняется внутри rca,
  а изменение презентации — внутри presentation.
- inputs содержит только JSON-совместимые параметры и artifact refs.
- Planner ничего не ищет, не вызывает tools, не сохраняет артефакты и не
  отвечает за выполнение шагов.

Пример запроса:
«Найди инциденты за май, какие сервисы чаще всего инцидентили, и сделай RCA
INC-123».

Пример структуры plan.steps:
[
  {
    "step_id": "search_may_incidents",
    "kind": "search",
    "goal": "Найти инциденты за май и посчитать количество по сервисам",
    "depends_on": [],
    "inputs": {
      "query": "инциденты за май",
      "aggregations": [
        {
          "group_by": "service",
          "metric": "incident_count",
          "sort": "desc",
          "limit": 10
        }
      ]
    },
    "user_visible_label": "Инциденты за май и сервисы"
  },
  {
    "step_id": "search_inc_123",
    "kind": "search",
    "goal": "Найти инцидент INC-123",
    "depends_on": [],
    "inputs": {
      "incident_number": "INC-123"
    },
    "user_visible_label": "Инцидент INC-123"
  },
  {
    "step_id": "rca_inc_123",
    "kind": "rca",
    "goal": "Провести RCA по INC-123",
    "depends_on": ["search_inc_123"],
    "inputs": {
      "source_step_id": "search_inc_123"
    },
    "user_visible_label": "RCA INC-123"
  }
]

Для kind="execute":
- plan_id создай как короткий читаемый технический идентификатор без пробелов;
- plan.goal — цель пользователя;
- plan.created_at заполни UTC ISO-датой из runtime_context.current_time.
"""


def _recent_messages(
    state: ConversationState,
    *,
    limit: int = 12,
) -> list[BaseMessage]:
    messages = list(state["messages"][-limit:])

    if messages:
        return messages

    return [HumanMessage(content="Пользователь ещё не отправил сообщение.")]


def _artifact_context(
    state: ConversationState,
) -> list[dict[str, str | None]]:
    artifacts: list[ArtifactRef] = []

    current_report_ref = state.get("current_report_ref")
    if current_report_ref is not None:
        artifacts.append(current_report_ref)

    current_presentation_ref = state.get("current_presentation_ref")
    if current_presentation_ref is not None:
        artifacts.append(current_presentation_ref)

    return [artifact.model_dump(mode="json") for artifact in artifacts]


async def plan_message(
    state: ConversationState,
    *,
    current_time: str,
) -> PlannerDecision:
    """
    Строит полный DAG для нового пользовательского запроса.

    Planner видит только историю основного чата и безопасные ссылки на уже
    сохранённые результаты. Он не получает локальные переписки worker-ов,
    сырые результаты поиска и runtime dependencies.
    """
    system_message = llm_client.build_system_message(
        role_instruction=PLANNER_PROMPT,
        extra_context={
            "available_artifacts": _artifact_context(state),
            "current_time": current_time,
        },
        output_contract="JSON PlannerDecision.",
    )

    return await llm_client.ainvoke_structured(
        [system_message, *_recent_messages(state)],
        PlannerDecision,
        worker_kind="planner",
    )


def get_step_source_refs(
    *,
    plan: ExecutionPlan,
    step_id: str,
    completed_step_refs: dict[str, list[ArtifactRef]],
) -> list[ArtifactRef]:
    """
    Возвращает refs прямых зависимостей шага.

    Executor использует это при запуске worker-а: сам planner указывает
    dependency через depends_on, а worker получает реальные output refs
    завершённых upstream шагов отдельно от LLM-generated inputs.
    """
    step = next(
        (item for item in plan.steps if item.step_id == step_id),
        None,
    )
    if step is None:
        raise ValueError(f"Unknown plan step: {step_id!r}.")

    return [
        ref
        for dependency_id in step.depends_on
        for ref in completed_step_refs.get(dependency_id, [])
    ]
from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

from app.ai.schemas.conversation import ConversationPlan, DomainRef
from app.ai.schemas.conversation_state import ConversationState
from app.services.llm import llm_client


_PLANNER_FALLBACK_PROMPT = """
Ты — planner единого ассистента по IT-инцидентам, RCA-справкам,
поручениям и презентациям.

Определи одну главную цель последнего сообщения пользователя и верни JSON
строго по схеме ConversationPlan.

Доступные intent:

search:
- пользователь просит найти инциденты, поручения, мероприятия или похожие
  случаи;
- заполни search_query самостоятельной формулировкой задачи поиска;
- не ищи данные и не придумывай canonical ID, даты, номера или фильтры.

rca:
- пользователь просит провести RCA, разобраться в причине, подготовить
  справку по инциденту или проанализировать инцидент;
- если пользователь дал номер, заполни incident_number;
- если пользователь описал проблему, заполни raw_description;
- если сначала нужно найти конкретный инцидент, установи requires_search=true
  и заполни search_query;
- не выдавай предположение за incident_number.

presentation:
- пользователь просит слайды, презентацию или HTML-презентацию;
- если есть номер — заполни incident_number;
- если есть описание — заполни raw_description;
- если сначала нужен поиск конкретного инцидента — requires_search=true и
  search_query;
- не выставляй use_current_report=true без явного указания, что нужно
  использовать текущую/готовую RCA-справку.

edit:
- пользователь просит изменить готовую RCA-справку, её раздел или
  поручение;
- заполни edit_instruction;
- use_current_report=true допустим, если пользователь явно ссылается на
  текущую/последнюю справку;
- не выполняй редактирование, только сформируй план.

chat:
- приветствие, благодарность, вопрос о возможностях, просьба объяснить
  термин, непонятное сообщение или другой обычный диалог;
- заполни chat_response: готовый короткий ответ пользователю;
- не создавай поиск, RCA, edit или presentation без явной цели.

Правила:
- Planner не использует regex, tools, lookup или memory.
- Не придумывай номер инцидента, report ID, название системы, период,
  результат поиска или доказательства.
- Не превращай случайную фразу в задачу, если пользователь не обозначил
  понятную цель.
- Для одной реплики выбирай одно главное intent.
- Если пользователь просит найти инцидент и затем RCA/презентацию,
  выбирай конечную цель `rca` или `presentation`, а поиск обозначай
  `requires_search=true`.
"""


def _recent_messages(
    state: ConversationState,
    *,
    limit: int = 8,
) -> list[BaseMessage]:
    """
    Planner получает небольшой контекст, чтобы понимать «его», «эту справку»,
    «сделай презентацию» и иные продолжения диалога.

    Текущая user-реплика всегда добавляется последней, даже если messages
    содержит больше limit элементов.
    """
    messages = list(state["messages"][-limit:])

    if messages:
        return messages

    return [
        HumanMessage(
            content="Пользователь не передал сообщение."
        )
    ]


def _planner_context(
    state: ConversationState,
) -> dict:
    """
    Передаём Planner-у только доступные для осмысленной ссылки объекты.

    Полные artifact versions и содержимое reports сюда не отправляем:
    Planner должен знать, что есть текущая справка, но не должен заниматься
    её анализом или редактированием.
    """
    current_artifact_id = state.get("current_artifact_id")
    current_artifact = (
        state["artifacts"].get(current_artifact_id)
        if current_artifact_id
        else None
    )

    current_report: dict | None = None

    if (
        current_artifact is not None
        and current_artifact.get("kind") == "incident_report"
    ):
        current_report = {
            "id": current_artifact["id"],
            "kind": "incident_report",
            "version": current_artifact["current_version"],
        }

    last_search = state.get("last_search")

    serialized_last_search = (
        last_search.model_dump(mode="json")
        if last_search is not None
        else None
    )

    return {
        "last_search": serialized_last_search,
        "current_report": current_report,
        "available_refs": [
            {
                "kind": "incident_report",
                "id": current_report["id"],
                "label": "Текущая RCA-справка",
            }
        ]
        if current_report
        else [],
    }


def _resolve_current_report_ref(
    state: ConversationState,
    plan: ConversationPlan,
) -> ConversationPlan:
    """
    LLM не получает право придумывать artifact ID.

    Если plan легитимно ссылается на текущую RCA-справку через
    use_current_report, runtime подставляет реально существующий ref.
    """
    if not plan.use_current_report:
        return plan

    current_artifact_id = state.get("current_artifact_id")

    if not current_artifact_id:
        return plan

    artifact = state["artifacts"].get(current_artifact_id)

    if (
        artifact is None
        or artifact.get("kind") != "incident_report"
    ):
        return plan

    report_ref = DomainRef(
        kind="incident_report",
        id=artifact["id"],
        label="Текущая RCA-справка",
    )

    return plan.model_copy(
        update={
            "target_ref": report_ref,
        }
    )


async def plan_message(
    state: ConversationState,
) -> ConversationPlan:
    """
    Строит typed plan из новой пользовательской реплики.

    Подстановка текущего report ref происходит в Python после LLM:
    модель обозначает намерение использовать текущую справку, а runtime
    выдаёт только реально существующий stable ID.
    """
    system = llm_client.build_system_message(
        role_instruction=_PLANNER_FALLBACK_PROMPT,
        extra_context=_planner_context(state),
        output_contract="JSON строго по схеме ConversationPlan.",
    )

    plan = await llm_client.ainvoke_structured(
        [
            system,
            *_recent_messages(state),
        ],
        ConversationPlan,
        worker_kind="planner",
    )

    return _resolve_current_report_ref(
        state,
        plan,
    )
from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.messages import HumanMessage

from app.ai.runtime.agent_messages import to_llm_messages
from app.ai.schemas.conversation import AgentConversation
from app.ai.workflows.search.contracts import SearchPlan
from app.services.llm import llm_client


SEARCH_AGENT_PROMPT = """
Ты — Search Agent IT-ассистента.

На входе находится нормализованный запрос от внутреннего Resolver. Его
canonical значения уже проверены через каталог. Построй один безопасный
SearchPlan.

Верни только JSON SearchPlan.

Режим records:
- используй, если пользователь хочет конкретные incidents или assignments:
  список, записи, поручения, похожие инциденты;
- canonical system_name, work_group, executor_name и element_name из
  normalized request переноси в records.filters без изменения;
- если resolver указал неоднозначное или не найденное каталожное значение,
  верни mode=clarify;
- даты из normalized request переноси в filters:
  - incidents: start_time_from / start_time_to;
  - assignments по сроку: deadline_from / deadline_to;
  - assignments по назначению: assigned_at_from / assigned_at_to;
- top_n только при явном ограничении: «топ 10», «первые 5», «один», «самый»;
  иначе top_n=null;
- sorts только при явном пользовательском указании порядка;
- records mode не содержит SQL.

Критическое правило semantic search:
- semantic создавай только для «похожие», «аналогичные», «схожие по смыслу»,
  «как этот инцидент»;
- текст для сравнения клади ТОЛЬКО в records.semantic.query;
- в records.filters запрещены text_error, description, text, query, error,
  message, symptom, title, details и любые другие поля свободного текста;
- обычный поиск по номеру, системе, дате или статусу не превращай в semantic.

Пример semantic:
{
  "mode": "records",
  "records": {
    "entity": "incidents",
    "filters": {},
    "semantic": {
      "query": "DNS timeout",
      "ranking": "primary",
      "candidate_limit": 200
    },
    "sorts": [],
    "top_n": null
  },
  "analytics": null,
  "question": null
}

Режим analytics:
- используй только для агрегатов, статистики, группировок, динамики,
  сравнений и рейтингов: «сколько», «статистика», «средний MTTR»,
  «топ услуг», «самый масштабный инцидент»;
- analytics.sql — ровно один SELECT или WITH ... SELECT;
- разрешены только таблицы analytics_incidents и analytics_assignments;
- запрещены incidents, assignments, messages, threads, entity_catalog и
  любые другие таблицы;
- запрещены INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, PRAGMA, ATTACH,
  DETACH, VACUUM, EXPLAIN и символ ;
- пользовательские значения передавай через ? placeholders и
  analytics.parameters;
- analytics.max_rows не больше 500.

Schema analytics_incidents:
number, status, priority_code, system_name, work_group, element_name,
executor_name, stand, start_time, end_time, mttd, mttr, downtime.

Schema analytics_assignments:
id, incident_id, ior, unit, responsible, created_by, status, deadline,
assigned_at.

Услуга или система в аналитике — system_name. Колонок service_name, service,
service_id не существует.

Пример analytics:
{
  "mode": "analytics",
  "records": null,
  "analytics": {
    "sql": "SELECT system_name AS service, COUNT(*) AS incidents_count FROM analytics_incidents WHERE start_time >= ? AND start_time <= ? GROUP BY system_name ORDER BY incidents_count DESC LIMIT 10",
    "parameters": [
      "2026-07-26T00:00:00Z",
      "2026-08-25T23:59:59Z"
    ],
    "max_rows": 10
  },
  "question": null
}

Режим clarify:
- используй, если entity, период, показатель или каталожный объект нельзя
  определить безопасно;
- верни только один короткий question;
- records и analytics должны быть null.

Filters incidents:
number, status, priority_code, system_name, work_group, element_name,
executor_name, stand, start_time_from, start_time_to, end_time_from,
end_time_to, mttd_min, mttd_max, mttr_min, mttr_max, downtime_min,
downtime_max.

Filters assignments:
id, incident_id, ior, unit, responsible, created_by, status, deadline_from,
deadline_to, assigned_at_from, assigned_at_to.

Sorts incidents:
start_time, end_time, mttd, mttr, downtime, priority_code.

Sorts assignments:
deadline, assigned_at.

Не изменяй canonical values из normalized request.
Не выдумывай колонки, которых нет в schema.
Не упоминай SQL, агентов, инструменты, workflow или внутреннюю реализацию.
"""


class SearchAgentError(RuntimeError):
    """Search Agent не вернул валидный строгий SearchPlan."""


async def plan_search(
    *,
    conversation: AgentConversation,
    normalized_request: str,
) -> SearchPlan:
    """
    Строит SearchPlan для нормализованного запроса.

    Локальная user/assistant переписка передаётся модели обычными messages.
    Нормализованный текст — технический вход текущего запуска, поэтому не
    записывается в StepRun.conversation.
    """
    request = normalized_request.strip()
    if not request:
        raise SearchAgentError("Normalized search request must not be empty.")

    system_message = llm_client.build_system_message(
        role_instruction=SEARCH_AGENT_PROMPT,
        extra_context={
            "current_datetime_utc": datetime.now(timezone.utc).isoformat(),
        },
        output_contract="JSON SearchPlan.",
    )

    try:
        return await llm_client.ainvoke_structured(
            [
                system_message,
                *to_llm_messages(conversation),
                HumanMessage(
                    content=(
                        "Построй SearchPlan для нормализованного запроса:\n"
                        f"{request}"
                    )
                ),
            ],
            SearchPlan,
            worker_kind="search_agent",
        )
    except Exception as exc:
        raise SearchAgentError(
            "Search Agent returned invalid output."
        ) from exc
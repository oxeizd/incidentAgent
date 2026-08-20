from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from app.ai.runtime.agent_history import (
    append_agent_event,
    get_agent_events,
)
from app.ai.schemas.conversation import TaskSnapshot
from app.ai.workflows.rca.contracts import (
    RCAGateDecision,
    RCAInput,
    RCAReportDraft,
)
from app.services.llm import llm_client


_AGENT_NAME = "rca_analyzer"
_HISTORY_LIMIT = 24


_ANALYZER_PROMPT = """
Ты — RCA Analyzer единого ассистента по IT-инцидентам.

Тебе переданы:
- RCA source с подтверждёнными данными инцидента или описанием;
- дополнительные evidence и ответы пользователя;
- структурированное решение RCA Gate.

Сформируй структурированный draft RCA-справки по схеме RCAReportDraft.

Правила фактической точности:

- Используй только факты из RCA source и Gate evidence.
- Не придумывай логи, метрики, номера инцидентов, даты, временные метки,
  системы, команды, пользователей, действия и результаты восстановления.
- Не выдавай hypothesis за fact.
- Если причина — hypothesis, явно зафиксируй это через
  root_cause_kind="hypothesis", limitations и open_questions.
- Если причина неизвестна, используй root_cause_kind="unknown"; не
  формулируй причинную цепочку как установленный факт.
- Если Gate уже определил root cause_kind/root_cause, не меняй их смысл
  и не повышай степень доказанности.

Справка должна включать:

- summary: короткое резюме происшествия и текущего статуса;
- affected_systems: только явно упомянутые системы/сервисы;
- symptoms, impact, timeline, applied_measures;
- facts: проверяемые evidence с корректным kind;
- root_cause, causal_chain, contributing_factors;
- corrective_actions и preventive_actions;
- open_questions и limitations;
- confidence и confidence_reason;
- analysis: готовый concise Markdown для показа пользователю.

Требования к `analysis`:

- Используй заголовки и короткие списки.
- Не повторяй одни и те же факты разными словами.
- Явно отделяй «Установленные факты», «Гипотеза» и «Ограничения», если
  это применимо.
- Не выдумывай пяти причин, если evidence не поддерживает такую цепочку.
- Не добавляй секцию только ради шаблона, если фактов для неё нет.

Требования к actions:

- Каждая мера должна устранять конкретную root cause или contributing
  factor из данного RCA.
- Мера описывает повторяемый технический/процессный механизм, а не разовое
  поручение человеку.
- Не предлагай общие фразы «улучшить мониторинг», «усилить контроль»,
  «провести ревью» без конкретного механизма, условия и ожидаемого
  результата.
- expected_result должен быть проверяемым: метрика, тест, наблюдаемое
  поведение или отсутствие конкретного класса ошибок.
- Если причина лишь hypothesis, description меры должен начинаться:
  «Если гипотеза подтвердится: ...».
- Максимум пять мер в сумме в corrective_actions + preventive_actions.
- Если осмысленные меры невозможно сформулировать без подтверждения
  причины, верни пустые списки, а не общие рекомендации.

Не раскрывай internal reasoning. Верни только JSON строго по схеме
RCAReportDraft.
"""


@dataclass(frozen=True, slots=True)
class AnalyzerOutcome:
    draft: RCAReportDraft
    snapshot_data: dict


async def run_analyzer(
    *,
    snapshot: TaskSnapshot,
    rca_input: RCAInput,
    gate: RCAGateDecision,
) -> AnalyzerOutcome:
    """
    Формирует draft справки после успешного Gate.

    В task history сохраняется только structured input/output, а не reasoning
    модели. Это поддерживает re-run после временной ошибки и даёт audit
    контекст до завершения RCA task.
    """
    data = append_agent_event(
        snapshot.data,
        agent=_AGENT_NAME,
        role="system",
        kind="analysis_input",
        payload={
            "rca_input": rca_input.model_dump(mode="json"),
            "gate": gate.model_dump(mode="json"),
        },
        max_events=_HISTORY_LIMIT,
    )

    system = llm_client.build_system_message(
        role_instruction=_ANALYZER_PROMPT,
        extra_context={
            "rca_input": rca_input.model_dump(mode="json"),
            "gate": gate.model_dump(mode="json"),
            "agent_history": get_agent_events(
                data,
                agent=_AGENT_NAME,
            ),
        },
        output_contract="JSON строго по схеме RCAReportDraft.",
    )

    draft = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(
                content=(
                    "Подготовь RCA-справку по подтверждённому "
                    "контексту и заключению Gate."
                )
            ),
        ],
        RCAReportDraft,
        worker_kind="rca_analyzer",
    )

    data = append_agent_event(
        data,
        agent=_AGENT_NAME,
        role="assistant",
        kind="report_draft",
        payload=draft.model_dump(mode="json"),
        max_events=_HISTORY_LIMIT,
    )

    return AnalyzerOutcome(
        draft=draft,
        snapshot_data=data,
    )
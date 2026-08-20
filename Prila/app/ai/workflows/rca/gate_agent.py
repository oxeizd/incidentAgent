from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from app.ai.runtime.agent_history import (
    append_agent_event,
    get_agent_events,
)
from app.ai.schemas.conversation import TaskSnapshot
from app.ai.workflows.rca.contracts import (
    RCAInput,
    RCAGateDecision,
)
from app.services.llm import llm_client


_AGENT_NAME = "rca_gate"
_HISTORY_LIMIT = 40


_GATE_PROMPT = """
Ты — RCA Gate единого ассистента по IT-инцидентам.

Тебе переданы:
- текущий RCA source;
- подтверждённые данные инцидента или описание пользователя;
- дополнительные evidence и ответы пользователя на прошлые уточнения;
- локальная история текущей RCA-задачи.

Твоя задача — не написать RCA-справку. Ты только определяешь, достаточно ли
данных для следующей стадии RCA-анализатора.

Разделяй:

- fact — подтверждённый факт из incident data, логов, метрик, timeline,
  результата rollback/fix или явного ответа пользователя;
- hypothesis — правдоподобная, но не подтверждённая причина/связь;
- unknown — данных нет или они недостаточны.

Root cause может быть:
- fact, только если есть конкретная причина и evidence, которые объясняют
  причинную связь;
- hypothesis, если есть рабочая версия, но доказательств недостаточно;
- unknown, если даже рабочую версию формулировать нельзя.

Верни action:

- "analyze", если контекста достаточно для полезной RCA-справки.
  Причина при этом может быть fact, hypothesis или unknown, но в последнем
  случае справка обязана явно фиксировать ограничения и открытые вопросы.
  Не добавляй questions.

- "clarify", если пользователь может дать конкретные недостающие сведения,
  которые существенно улучшат RCA. Задай от 1 до 3 коротких технических
  questions: например про компонент, предшествующее изменение, конкретный
  лог/метрику, timeline, rollback/fix и наблюдаемый результат.

- "stop", если источник не содержит инцидентных данных или невозможно
  сформулировать даже предметные уточняющие вопросы.

Правила:
- Не придумывай evidence, логи, метрики, даты, системы, действия или
  результаты восстановления.
- Не объявляй root cause фактом только по словам «причина», «из-за» или
  «root cause».
- Не задавай общий вопрос «расскажите подробнее».
- При сомнении между analyze и clarify выбери clarify.
- Не требуй искусственно пять why, если данные не поддерживают цепочку.
- Не раскрывай внутренние рассуждения, верни только JSON по схеме
  RCAGateDecision.
"""


@dataclass(frozen=True, slots=True)
class GateOutcome:
    decision: RCAGateDecision
    snapshot_data: dict[str, Any]


async def run_gate(
    *,
    snapshot: TaskSnapshot,
    rca_input: RCAInput,
) -> GateOutcome:
    """
    Выполняет одну итерацию RCA Gate.

    rca_input и local history сохраняются как JSON-safe события. На каждом
    уточняющем раунде workflow обновляет rca_input.user_evidence, затем
    повторно вызывает этот же agent без потери первоначального incident
    context.
    """
    data = append_agent_event(
        snapshot.data,
        agent=_AGENT_NAME,
        role="system",
        kind="rca_input",
        payload=rca_input.model_dump(mode="json"),
        max_events=_HISTORY_LIMIT,
    )

    system = llm_client.build_system_message(
        role_instruction=_GATE_PROMPT,
        extra_context={
            "rca_input": rca_input.model_dump(mode="json"),
            "verified_task_history": get_agent_events(
                data,
                agent=_AGENT_NAME,
            ),
        },
        output_contract="JSON строго по схеме RCAGateDecision.",
    )

    decision = await llm_client.ainvoke_structured(
        [
            system,
            HumanMessage(
                content=(
                    "Проверь достаточность текущего RCA-контекста."
                )
            ),
        ],
        RCAGateDecision,
        worker_kind="rca_gate",
    )

    data = append_agent_event(
        data,
        agent=_AGENT_NAME,
        role="assistant",
        kind="gate_decision",
        payload=decision.model_dump(mode="json"),
        max_events=_HISTORY_LIMIT,
    )

    return GateOutcome(
        decision=decision,
        snapshot_data=data,
    )
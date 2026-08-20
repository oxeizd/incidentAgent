from __future__ import annotations

from typing import Any

from app.ai.schemas.conversation import Interaction
from app.ai.workflows.search.contracts import (
    CatalogCandidate,
    SearchIncidentCandidate,
)


def catalog_candidates_for_ids(
    *,
    snapshot_data: dict[str, Any],
    candidate_ids: list[str],
    agent_name: str = "search_normalizer",
) -> list[CatalogCandidate]:
    """
    Возвращает catalog candidates только по указанным IDs.

    Candidates извлекаются исключительно из сохранённых реальных tool results
    Search Normalizer-а. Функция не делает lookup заново и ничего не
    выбирает/ранжирует.
    """
    by_id: dict[str, CatalogCandidate] = {}

    agent_history = snapshot_data.get(
        "agent_history",
        {},
    )

    history = agent_history.get(agent_name, {})

    for event in history.get("events", []):
        if event.get("role") != "tool":
            continue

        if event.get("kind") != "lookup_entity":
            continue

        raw_candidates = event.get(
            "payload",
            {},
        ).get("candidates", [])

        if not isinstance(raw_candidates, list):
            continue

        for raw in raw_candidates:
            try:
                candidate = CatalogCandidate.model_validate(raw)
            except Exception:
                continue

            by_id[candidate.id] = candidate

    return [
        by_id[candidate_id]
        for candidate_id in candidate_ids
        if candidate_id in by_id
    ]


def catalog_candidate_label(
    candidate: CatalogCandidate,
) -> str:
    """
    Короткое, понятное пользователю название варианта каталога.
    """
    type_labels = {
        "system_name": "Система",
        "work_group": "Рабочая группа",
        "executor_name": "Исполнитель",
        "element_name": "Элемент",
    }

    return (
        f"{type_labels[candidate.entity_type]}: {candidate.name}"
    )


def incident_candidates_from_preview(
    rows: list[Any],
) -> list[SearchIncidentCandidate]:
    """
    Формирует incident candidates из preview persisted Search artifact.

    Boundary contract: incident display profile должен содержать `number`.
    Если номера нет, строка не может стать IncidentRef и не участвует
    в selection; entity_id не подменяет incident number.
    """
    candidates: list[SearchIncidentCandidate] = []

    for row in rows:
        values = getattr(row, "values", None)

        if not isinstance(values, dict):
            continue

        number = values.get("number")

        if not isinstance(number, str) or not number.strip():
            continue

        description = values.get("description") or ""
        system_name = values.get("system_name") or ""

        label_parts = [
            number.strip(),
            str(description).strip(),
            str(system_name).strip(),
        ]

        label = " — ".join(
            value
            for value in label_parts
            if value
        )

        candidates.append(
            SearchIncidentCandidate(
                entity_id=row.entity_id,
                number=number.strip(),
                label=label,
            )
        )

    return candidates


def render_interaction_text(
    interaction: Interaction,
) -> str:
    """
    Временный renderer до подключения structured UI.

    Interaction по-прежнему сохраняет kind/options/fields и может быть
    отдан API отдельно. Этот текстовый renderer нужен только для текущего
    chat transport.
    """
    lines = [interaction.question]

    if interaction.options:
        lines.extend(
            [
                "",
                *[
                    f"{index}. {option.label}"
                    for index, option in enumerate(
                        interaction.options,
                        start=1,
                    )
                ],
                "",
                "Ответьте обычным текстом.",
            ]
        )

    return "\n".join(lines)
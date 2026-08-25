from __future__ import annotations

from typing import Any


STATUS_META = {
    "NEW": ("🆕", "Новое"),
    "PARTIAL": ("⚠️", "Частично совпадает"),
    "DUPLICATE": ("♻️", "Дубликат"),
    "INVALID": ("⛔", "Не подходит"),
}

TYPE_META = {
    "architecture": ("🏗️", "Архитектура"),
    "config": ("⚙️", "Конфигурация"),
    "process": ("📋", "Процесс"),
    "monitoring": ("📊", "Мониторинг"),
    "automation": ("🤖", "Автоматизация"),
}

PRIORITY_META = {
    "high": ("🔴", "Высокий"),
    "medium": ("🟡", "Средний"),
    "low": ("🟢", "Низкий"),
}

CONFIDENCE_META = {
    "high": ("🟢", "Высокая"),
    "medium": ("🟡", "Средняя"),
    "low": ("🔴", "Низкая"),
}

ROOT_CAUSE_META = {
    "fact": "Подтверждённый факт",
    "hypothesis": "Гипотеза",
    "unknown": "Не установлена",
}


def render_incident_report_markdown(
    *,
    report_id: str,
    status: str,
    current_version: int,
    sections: dict[str, Any],
) -> str:
    """
    Строит user-facing Markdown из persisted RCA sections.

    Не использует HTML/CSS. Работает и для legacy sections, где часть
    данных находится под `_rca`, и для текущего формата top-level fields.
    """
    rca = _as_dict(sections.get("_rca"))
    draft = _as_dict(rca.get("draft"))

    summary = _text(sections.get("summary")) or _text(draft.get("summary"))
    analysis = _text(sections.get("analysis")) or _text(draft.get("analysis"))

    symptoms = _string_list(
        sections.get("symptoms") or draft.get("symptoms")
    )
    impact = _string_list(
        sections.get("impact") or draft.get("impact")
    )
    timeline = _string_list(
        sections.get("timeline") or draft.get("timeline")
    )

    root_cause = (
        _text(sections.get("root_cause"))
        or _text(draft.get("root_cause"))
    )
    root_cause_kind = (
        _text(sections.get("root_cause_kind"))
        or _text(draft.get("root_cause_kind"))
        or "unknown"
    )

    causal_chain = _string_list(
        sections.get("causal_chain") or draft.get("causal_chain")
    )
    contributing_factors = _string_list(
        sections.get("contributing_factors")
        or draft.get("contributing_factors")
    )
    applied_measures = _string_list(
        sections.get("applied_measures")
        or draft.get("applied_measures")
    )

    corrective_actions = _dict_list(
        sections.get("corrective_actions")
        or draft.get("corrective_actions")
    )
    preventive_actions = _dict_list(
        sections.get("preventive_actions")
        or draft.get("preventive_actions")
    )

    accepted_tasks = _dict_list(
        _as_dict(rca.get("validation")).get("accepted_tasks")
    )
    rejected_tasks = _dict_list(
        _as_dict(rca.get("validation")).get("rejected_tasks")
    )

    tasks = _dict_list(sections.get("tasks"))
    if not tasks:
        tasks = _merge_tasks(
            corrective_actions=corrective_actions,
            preventive_actions=preventive_actions,
            accepted_tasks=accepted_tasks,
            rejected_tasks=rejected_tasks,
        )

    open_questions = _string_list(
        sections.get("open_questions") or draft.get("open_questions")
    )
    limitations = _string_list(
        sections.get("limitations") or draft.get("limitations")
    )

    confidence = (
        _text(sections.get("confidence"))
        or _text(draft.get("confidence"))
        or "low"
    )
    confidence_reason = (
        _text(sections.get("confidence_reason"))
        or _text(draft.get("confidence_reason"))
    )

    confidence_icon, confidence_label = CONFIDENCE_META.get(
        confidence,
        ("⚪", confidence),
    )

    lines = [
        "# RCA-справка",
        "",
        (
            f"> **Статус:** {_status_label(status)}  "
            f"· **Версия:** {current_version}  "
            f"· **Уверенность:** {confidence_icon} {confidence_label}"
        ),
        "",
    ]

    if summary:
        lines.extend(
            [
                "## Краткое резюме",
                "",
                summary,
                "",
            ]
        )

    if symptoms or impact:
        lines.extend(
            [
                "## Симптомы и влияние",
                "",
            ]
        )

        if symptoms:
            lines.extend(
                [
                    "### Наблюдаемые симптомы",
                    "",
                    *_bullets(symptoms),
                    "",
                ]
            )

        if impact:
            lines.extend(
                [
                    "### Влияние",
                    "",
                    *_bullets(impact),
                    "",
                ]
            )

    lines.extend(
        _render_root_cause(
            root_cause=root_cause,
            root_cause_kind=root_cause_kind,
            causal_chain=causal_chain,
            contributing_factors=contributing_factors,
        )
    )

    if timeline:
        lines.extend(
            [
                "## Хронология",
                "",
                *_numbered(timeline),
                "",
            ]
        )

    if applied_measures:
        lines.extend(
            [
                "## Временная стабилизация",
                "",
                *_bullets(applied_measures),
                "",
            ]
        )

    if tasks:
        lines.extend(
            _render_tasks(
                tasks=tasks,
                accepted_count=len(accepted_tasks),
                rejected_count=len(rejected_tasks),
            )
        )

    if open_questions or limitations:
        lines.extend(
            [
                "## Неопределённости и дальнейшие проверки",
                "",
            ]
        )

        if open_questions:
            lines.extend(
                [
                    "### Открытые вопросы",
                    "",
                    *_bullets(open_questions),
                    "",
                ]
            )

        if limitations:
            lines.extend(
                [
                    "### Ограничения анализа",
                    "",
                    *_bullets(limitations),
                    "",
                ]
            )

    if confidence_reason:
        lines.extend(
            [
                "## Оценка уверенности",
                "",
                f"**{confidence_icon} {confidence_label}.** "
                f"{confidence_reason}",
                "",
            ]
        )

    if analysis:
        lines.extend(
            [
                "---",
                "",
                "## Развёрнутый анализ",
                "",
                analysis,
                "",
            ]
        )

    lines.extend(
        [
            "---",
            "",
            f"`RCA ID: {report_id}`",
        ]
    )

    return "\n".join(lines).strip()


def _render_root_cause(
    *,
    root_cause: str | None,
    root_cause_kind: str,
    causal_chain: list[str],
    contributing_factors: list[str],
) -> list[str]:
    label = ROOT_CAUSE_META.get(root_cause_kind, root_cause_kind)

    lines = [
        "## Причина и механизм сбоя",
        "",
    ]

    if root_cause:
        lines.extend(
            [
                f"**Корневая причина — {label}:** {root_cause}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"**Корневая причина:** {label}.",
                "",
            ]
        )

    if causal_chain:
        lines.extend(
            [
                "### Причинно-следственная цепочка",
                "",
                *_numbered(causal_chain),
                "",
            ]
        )

    if contributing_factors:
        lines.extend(
            [
                "### Способствующие факторы",
                "",
                *_bullets(contributing_factors),
                "",
            ]
        )

    return lines


def _render_tasks(
    *,
    tasks: list[dict[str, Any]],
    accepted_count: int,
    rejected_count: int,
) -> list[str]:
    counts: dict[str, int] = {
        "NEW": 0,
        "PARTIAL": 0,
        "DUPLICATE": 0,
        "INVALID": 0,
    }

    for task in tasks:
        status = _text(task.get("validation_status")) or "NEW"
        counts[status] = counts.get(status, 0) + 1

    lines = [
        "## План корректирующих и профилактических действий",
        "",
        (
            f"> **Всего:** {len(tasks)} · "
            f"🆕 Новых: {counts.get('NEW', 0)} · "
            f"⚠️ Частичных: {counts.get('PARTIAL', 0)} · "
            f"♻️ Дубликатов: {counts.get('DUPLICATE', 0)}"
        ),
        "",
        "| # | Мера | Тип | Приоритет | Проверка |",
        "|---:|---|---|---|---|",
    ]

    for index, task in enumerate(tasks, start=1):
        title = _text(task.get("title")) or "Без названия"
        task_type = _text(task.get("type")) or "—"
        priority = _text(task.get("priority")) or "—"
        status = _text(task.get("validation_status")) or "NEW"

        type_icon, type_label = TYPE_META.get(
            task_type,
            ("•", task_type),
        )
        priority_icon, priority_label = PRIORITY_META.get(
            priority,
            ("•", priority),
        )
        status_icon, status_label = STATUS_META.get(
            status,
            ("•", status),
        )

        lines.append(
            "| "
            f"{index} | {_table_text(title)} | "
            f"{type_icon} {type_label} | "
            f"{priority_icon} {priority_label} | "
            f"{status_icon} {status_label} |"
        )

    lines.append("")

    for index, task in enumerate(tasks, start=1):
        title = _text(task.get("title")) or "Без названия"
        description = _text(task.get("description"))
        addresses = _text(task.get("addresses"))
        expected_result = _text(task.get("expected_result"))
        validation_status = _text(task.get("validation_status")) or "NEW"
        validation_reason = _text(task.get("validation_reason"))

        status_icon, status_label = STATUS_META.get(
            validation_status,
            ("•", validation_status),
        )

        lines.extend(
            [
                "<details>",
                f"<summary><strong>{index}. {title}</strong></summary>",
                "",
            ]
        )

        if description:
            lines.extend(
                [
                    "**Описание**",
                    "",
                    description,
                    "",
                ]
            )

        if addresses:
            lines.extend(
                [
                    f"**Устраняет:** {addresses}",
                    "",
                ]
            )

        if expected_result:
            lines.extend(
                [
                    f"**Ожидаемый результат:** {expected_result}",
                    "",
                ]
            )

        lines.extend(
            [
                f"**Статус проверки:** {status_icon} {status_label}",
                "",
            ]
        )

        if validation_reason:
            lines.extend(
                [
                    f"**Обоснование:** {validation_reason}",
                    "",
                ]
            )

        similar = task.get("most_similar_assignment")
        if isinstance(similar, dict):
            similar_title = (
                _text(similar.get("assignment"))
                or _text(similar.get("task"))
                or _text(similar.get("id"))
            )

            if similar_title:
                lines.extend(
                    [
                        f"**Похожее существующее поручение:** {similar_title}",
                        "",
                    ]
                )

        lines.extend(
            [
                "</details>",
                "",
            ]
        )

    if accepted_count or rejected_count:
        lines.extend(
            [
                (
                    f"_Принято после проверки: {accepted_count}. "
                    f"Отклонено: {rejected_count}._"
                ),
                "",
            ]
        )

    return lines


def _merge_tasks(
    *,
    corrective_actions: list[dict[str, Any]],
    preventive_actions: list[dict[str, Any]],
    accepted_tasks: list[dict[str, Any]],
    rejected_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    validated_by_title: dict[str, dict[str, Any]] = {}

    for task in [*accepted_tasks, *rejected_tasks]:
        title = _text(task.get("title"))

        if title:
            validated_by_title[title] = task

    merged: list[dict[str, Any]] = []

    for task in [*corrective_actions, *preventive_actions]:
        title = _text(task.get("title"))
        validated = validated_by_title.get(title or "")

        if validated is not None:
            merged.append(validated)
        else:
            merged.append(task)

    return merged


def _status_label(status: str) -> str:
    normalized = status.strip().lower()

    if normalized == "final":
        return "✅ Финальная"

    if normalized == "draft":
        return "📝 Черновик"

    return normalized or "—"


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    return [
        item
        for item in value
        if isinstance(item, dict)
    ]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    result: list[str] = []

    for item in value:
        text = _text(item)

        if text:
            result.append(text)

    return result


def _bullets(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values]


def _numbered(values: list[str]) -> list[str]:
    return [
        f"{index}. {value}"
        for index, value in enumerate(values, start=1)
    ]


def _table_text(value: str) -> str:
    return " ".join(
        value.replace("|", "\\|").split()
    )
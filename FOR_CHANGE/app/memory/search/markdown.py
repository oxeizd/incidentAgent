from __future__ import annotations

from memory.search.contracts import SearchResultReferenceArtifact


def render_search_preview_markdown(
    artifact: SearchResultReferenceArtifact,
) -> str:
    display = artifact.display
    preview = artifact.preview.rows

    if artifact.total_count == 0:
        return f"### {display.title}\n\nНичего не найдено."

    lines = [
        f"### {display.title}",
        "",
        (
            f"Найдено: **{artifact.total_count}**. "
            f"В сообщении показаны первые **{artifact.preview_count}**."
        ),
        "",
        "| " + " | ".join(_escape(column.label) for column in display.columns) + " |",
        "| " + " | ".join("---" for _ in display.columns) + " |",
    ]

    for row in preview:
        cells = [
            _escape(row.values.get(column.key, "—"))
            for column in display.columns
        ]
        lines.append("| " + " | ".join(cells) + " |")

    if artifact.preview_count < artifact.total_count:
        lines.extend(
            [
                "",
                "Полная таблица доступна в артефакте результата.",
            ]
        )

    return "\n".join(lines)


def _escape(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )
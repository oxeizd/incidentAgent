from __future__ import annotations

from app.memory.search.contracts import SearchResultReferenceArtifact


def render_search_preview_markdown(
    artifact: SearchResultReferenceArtifact,
) -> str:
    """
    Render compact chat-safe Markdown preview.

    Это fallback для chat history и простых clients. Полная таблица
    открывается UI через result_id, поэтому renderer не знает HTTP routes.
    """
    display = artifact.display
    preview_rows = artifact.preview.rows

    if artifact.total_count == 0:
        return f"### {display.title}\n\nНичего не найдено."

    lines = [
        f"### {display.title}",
        "",
        _render_count_line(artifact),
        "",
        _render_header(display),
        _render_separator(display),
    ]

    for row in preview_rows:
        lines.append(_render_row(artifact, row.values))

    if artifact.preview_count < artifact.total_count:
        lines.extend(
            [
                "",
                (
                    "Полный результат доступен по идентификатору: "
                    f"`{artifact.result_id}`."
                ),
            ]
        )

    return "\n".join(lines)


def _render_count_line(
    artifact: SearchResultReferenceArtifact,
) -> str:
    line = f"Найдено: **{artifact.total_count}**."

    if artifact.preview_count < artifact.total_count:
        return (
            f"{line} Показаны первые "
            f"**{artifact.preview_count}**."
        )

    return line


def _render_header(
    artifact: SearchResultReferenceArtifact,
) -> str:
    return (
        "| "
        + " | ".join(
            _escape_markdown(column.label)
            for column in artifact.display.columns
        )
        + " |"
    )


def _render_separator(
    artifact: SearchResultReferenceArtifact,
) -> str:
    return (
        "| "
        + " | ".join("---" for _ in artifact.display.columns)
        + " |"
    )


def _render_row(
    artifact: SearchResultReferenceArtifact,
    values: dict[str, str],
) -> str:
    return (
        "| "
        + " | ".join(
            _escape_markdown(
                values.get(column.key, "—")
            )
            for column in artifact.display.columns
        )
        + " |"
    )


def _escape_markdown(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )
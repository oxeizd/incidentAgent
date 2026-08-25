from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


AnalyticsParameter = str | int | float | bool | None


class AnalyticsSqlRequest(BaseModel):
    """
    Один read-only SQL запрос от AI-агента.

    Агент передаёт параметры отдельным массивом и не конкатенирует
    пользовательский текст в SQL. Разрешён один SELECT/WITH ... SELECT
    только по analytics views.
    """

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(
        min_length=1,
        max_length=20_000,
    )
    parameters: list[AnalyticsParameter] = Field(
        default_factory=list,
        max_length=100,
    )
    max_rows: int = Field(
        default=100,
        ge=1,
        le=500,
    )


class AnalyticsSqlResult(BaseModel):
    """
    Bounded JSON-safe ответ аналитического SQL tool.

    truncated=True означает, что в БД было больше строк, чем max_rows.
    """

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
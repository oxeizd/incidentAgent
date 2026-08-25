from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


IncidentReportStatus = Literal[
    "draft",
    "final",
]


class IncidentReportVersion(BaseModel):
    """
    Неизменяемый snapshot одной версии RCA-справки.

    `sections` — полный JSON-safe документ отчёта. При любом подтверждённом
    изменении создаётся новая версия вместо изменения существующей.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    sections: dict[str, Any] = Field(default_factory=dict)

    created_at: str = Field(min_length=1)
    created_by_task_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2_000)


class IncidentReportRecord(BaseModel):
    """
    Persistent RCA-справка.

    Report является самостоятельным domain object, а не частью
    ConversationState. Workflow и UI передают только IncidentReportRef.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)

    status: IncidentReportStatus = "draft"
    current_version: int = Field(ge=0)
    versions: list[IncidentReportVersion] = Field(
        min_length=1,
    )

    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_versions(self) -> "IncidentReportRecord":
        versions_by_number = {
            version.version: version
            for version in self.versions
        }

        if len(versions_by_number) != len(self.versions):
            raise ValueError(
                "incident report versions must be unique"
            )

        if self.current_version not in versions_by_number:
            raise ValueError(
                "current_version must exist in versions"
            )

        return self

    @property
    def current(self) -> IncidentReportVersion:
        for version in self.versions:
            if version.version == self.current_version:
                return version

        raise RuntimeError(
            "IncidentReportRecord current_version is invalid"
        )

    @property
    def sections(self) -> dict[str, Any]:
        """
        Удобный read-only contract для workflow/UI.

        Не модифицируй возвращённый dict in-place: repository при update
        создаёт новую immutable version.
        """
        return self.current.sections
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ImportEntity = Literal["incidents", "assignments"]
ImportStatus = Literal["completed", "completed_with_errors", "failed"]


class ImportErrorItem(BaseModel):
    """One rejected input item or post-import processing error."""

    model_config = ConfigDict(extra="forbid")

    index: int | None = Field(default=None, ge=0)
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2_000)


class ImportReport(BaseModel):
    """
    Итог одной import operation.

    imported_count — число успешно обработанных source records.
    failed_count — число source records, которые не были сохранены.
    warnings — ошибки derived processing, которые не отменяют import,
    например ошибка в vector indexing или catalog rebuild.
    """

    model_config = ConfigDict(extra="forbid")

    entity: ImportEntity
    status: ImportStatus

    total_items: int = Field(ge=0)
    imported_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)

    errors: list[ImportErrorItem] = Field(default_factory=list)
    warnings: list[ImportErrorItem] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "completed"


class ImportRequest(BaseModel):
    """
    Внутренний request contract для UI/API/background import.

    `source_name` не влияет на обработку; он нужен только для audit/log.
    """

    model_config = ConfigDict(extra="forbid")

    entity: ImportEntity
    source_name: str | None = Field(
        default=None,
        max_length=500,
    )
    max_errors: int = Field(
        default=100,
        ge=1,
        le=1_000,
    )
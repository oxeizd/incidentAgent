from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ImportEntity = Literal["incidents", "assignments"]


class ImportErrorItem(BaseModel):
    """A single rejected source record; no raw payload is persisted here."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=2_000)


class ImportReport(BaseModel):
    """Outcome of one import request."""

    model_config = ConfigDict(extra="forbid")

    entity: ImportEntity
    total_items: int = Field(ge=0)
    imported_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    errors: list[ImportErrorItem] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.failed_count == 0
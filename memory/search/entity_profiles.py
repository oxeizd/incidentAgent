from __future__ import annotations

from dataclasses import dataclass

from app.memory.search.contracts import DisplaySchema, EntityType
from app.memory.search.profiles import get_display_profile


@dataclass(frozen=True, slots=True)
class EntityDisplayProfiles:
    """
    Immutable display schema pair for one search entity.

    chat_preview используется в response artifact.
    table используется при сохранении immutable search result snapshot.
    """

    preview_name: str
    table_name: str

    @property
    def preview(self) -> DisplaySchema:
        return get_display_profile(self.preview_name)

    @property
    def table(self) -> DisplaySchema:
        return get_display_profile(self.table_name)


ENTITY_DISPLAY_PROFILES: dict[EntityType, EntityDisplayProfiles] = {
    "incidents": EntityDisplayProfiles(
        preview_name="incidents.chat_preview.v1",
        table_name="incidents.table.v1",
    ),
    "assignments": EntityDisplayProfiles(
        preview_name="assignments.chat_preview.v1",
        table_name="assignments.table.v1",
    ),
}


def get_entity_display_profiles(
    entity: EntityType,
) -> EntityDisplayProfiles:
    try:
        return ENTITY_DISPLAY_PROFILES[entity]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported search entity: {entity!r}"
        ) from exc
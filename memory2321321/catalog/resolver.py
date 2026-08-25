from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.memory.catalog.contracts import (
    ENTITY_CATALOG_TYPES,
    EntityCatalogEntry,
    EntityCatalogType,
    normalize_catalog_value,
)
from app.memory.catalog.service import EntityCatalogService


CYRILLIC_TO_LATIN_HOMOGLYPHS = {
    "а": "a",
    "А": "a",
    "е": "e",
    "Е": "e",
    "к": "k",
    "К": "k",
    "м": "m",
    "М": "m",
    "н": "h",
    "Н": "h",
    "о": "o",
    "О": "o",
    "р": "p",
    "Р": "p",
    "с": "c",
    "С": "c",
    "т": "t",
    "Т": "t",
    "х": "x",
    "Х": "x",
    "у": "y",
    "У": "y",
    "в": "b",
    "В": "b",
}

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class EntityResolverSettings:
    threshold: float = 65.0
    ambiguous_gap: float = 8.0
    cross_type_gap: float = 10.0
    cache_ttl_seconds: float = 60.0
    max_top_k: int = 20


@dataclass(frozen=True, slots=True)
class _ScoredEntry:
    entity_type: EntityCatalogType
    entry: EntityCatalogEntry
    score: float


class EntityCatalogResolver:
    """
    DB-backed replacement for legacy JSON EntityResolver.

    БД — source of truth. Полный catalog snapshot загружается с TTL
    cache и оценивается тем же fuzzy алгоритмом, что применялся к JSON:
    token set, plain ratio, Cyrillic/Latin homoglyphs и token containment.
    """

    def __init__(
        self,
        *,
        catalog: EntityCatalogService,
        settings: EntityResolverSettings | None = None,
    ) -> None:
        self._catalog = catalog
        self._settings = settings or EntityResolverSettings()
        self._cache: dict[
            EntityCatalogType,
            list[EntityCatalogEntry],
        ] = {
            entity_type: []
            for entity_type in ENTITY_CATALOG_TYPES
        }
        self._cache_expires_at = 0.0
        self._cache_lock = asyncio.Lock()

    async def resolve(
        self,
        *,
        query: str,
        entity_type: EntityCatalogType,
        top_k: int = 5,
    ) -> dict[str, Any]:
        normalized_query = _require_query(query)
        entries = (await self._snapshot())[entity_type]
        limit = self._normalize_top_k(top_k)

        exact_entries = [
            entry
            for entry in entries
            if _matches_exact(normalized_query, entry)
        ]

        if len(exact_entries) == 1:
            return {
                "status": "exact",
                "value": exact_entries[0].canonical_value,
                "candidates": [],
            }

        if len(exact_entries) > 1:
            return {
                "status": "ambiguous",
                "value": None,
                "candidates": [
                    (entry.canonical_value, 100.0)
                    for entry in exact_entries[:limit]
                ],
            }

        scored = _score_entries(
            query=normalized_query,
            entity_type=entity_type,
            entries=entries,
        )

        if not scored or scored[0].score < self._settings.threshold:
            return {
                "status": "not_found",
                "value": None,
                "candidates": [
                    (item.entry.canonical_value, item.score)
                    for item in scored[:limit]
                ],
            }

        best = scored[0]
        close = [
            item
            for item in scored
            if best.score - item.score
            <= self._settings.ambiguous_gap
        ]

        if len(close) > 1:
            return {
                "status": "ambiguous",
                "value": None,
                "candidates": [
                    (item.entry.canonical_value, item.score)
                    for item in close[:limit]
                ],
            }

        return {
            "status": "exact",
            "value": best.entry.canonical_value,
            "candidates": [],
        }

    async def resolve_any(
        self,
        *,
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        normalized_query = _require_query(query)
        snapshot = await self._snapshot()
        limit = self._normalize_top_k(top_k)

        exact_hits = [
            (entity_type, entry)
            for entity_type, entries in snapshot.items()
            for entry in entries
            if _matches_exact(normalized_query, entry)
        ]

        if len(exact_hits) == 1:
            entity_type, entry = exact_hits[0]
            return {
                "status": "exact",
                "entity_type": entity_type,
                "value": entry.canonical_value,
                "candidates": [],
            }

        if len(exact_hits) > 1:
            return {
                "status": "ambiguous",
                "entity_type": None,
                "value": None,
                "candidates": [
                    _candidate_payload(
                        entity_type=entity_type,
                        entry=entry,
                        score=100.0,
                    )
                    for entity_type, entry in exact_hits[:limit]
                ],
            }

        scored = [
            item
            for entity_type, entries in snapshot.items()
            for item in _score_entries(
                query=normalized_query,
                entity_type=entity_type,
                entries=entries,
            )
        ]

        if not scored:
            return {
                "status": "not_found",
                "entity_type": None,
                "value": None,
                "candidates": [],
            }

        scored.sort(key=_scored_sort_key)
        best = scored[0]

        if best.score < self._settings.threshold:
            return {
                "status": "not_found",
                "entity_type": None,
                "value": None,
                "candidates": [
                    _candidate_payload(
                        entity_type=item.entity_type,
                        entry=item.entry,
                        score=item.score,
                    )
                    for item in scored[:limit]
                ],
            }

        close_same_type = [
            item
            for item in scored
            if item.entity_type == best.entity_type
            and best.score - item.score
            <= self._settings.ambiguous_gap
        ]
        close_cross_type = [
            item
            for item in scored
            if item.entity_type != best.entity_type
            and best.score - item.score
            <= self._settings.cross_type_gap
        ]
        close = _deduplicate_scored(
            [*close_same_type, *close_cross_type]
        )

        if len(close) > 1:
            return {
                "status": "ambiguous",
                "entity_type": None,
                "value": None,
                "candidates": [
                    _candidate_payload(
                        entity_type=item.entity_type,
                        entry=item.entry,
                        score=item.score,
                    )
                    for item in close[:limit]
                ],
            }

        return {
            "status": "exact",
            "entity_type": best.entity_type,
            "value": best.entry.canonical_value,
            "candidates": [],
        }

    async def refresh(self) -> None:
        """Forces next request to reload the catalog from DB."""
        async with self._cache_lock:
            self._cache_expires_at = 0.0

    async def _snapshot(
        self,
    ) -> dict[EntityCatalogType, list[EntityCatalogEntry]]:
        now = time.monotonic()

        if now < self._cache_expires_at:
            return self._cache

        async with self._cache_lock:
            now = time.monotonic()

            if now < self._cache_expires_at:
                return self._cache

            entries = await self._catalog.list_all()
            refreshed = {
                entity_type: []
                for entity_type in ENTITY_CATALOG_TYPES
            }

            for entry in entries:
                refreshed[entry.entity_type].append(entry)

            self._cache = refreshed
            self._cache_expires_at = (
                now + self._settings.cache_ttl_seconds
            )
            return self._cache

    def _normalize_top_k(self, top_k: int) -> int:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")

        return min(top_k, self._settings.max_top_k)


def _score_entries(
    *,
    query: str,
    entity_type: EntityCatalogType,
    entries: list[EntityCatalogEntry],
) -> list[_ScoredEntry]:
    scored = [
        _ScoredEntry(
            entity_type=entity_type,
            entry=entry,
            score=max(
                (
                    _combined_score(query, value)
                    for value in (
                        entry.canonical_value,
                        *entry.aliases,
                    )
                ),
                default=0.0,
            ),
        )
        for entry in entries
    ]

    scored.sort(key=_scored_sort_key)
    return scored


def _scored_sort_key(
    item: _ScoredEntry,
) -> tuple[float, int, str, str]:
    return (
        -item.score,
        -item.entry.source_count,
        item.entity_type,
        item.entry.canonical_value,
    )


def _deduplicate_scored(
    values: list[_ScoredEntry],
) -> list[_ScoredEntry]:
    unique: dict[tuple[str, str], _ScoredEntry] = {}

    for item in values:
        key = (
            item.entity_type,
            item.entry.canonical_value,
        )
        previous = unique.get(key)

        if previous is None or item.score > previous.score:
            unique[key] = item

    return sorted(unique.values(), key=_scored_sort_key)


def _matches_exact(
    query: str,
    entry: EntityCatalogEntry,
) -> bool:
    normalized_query = normalize_catalog_value(query)

    if normalized_query == entry.normalized_value:
        return True

    return any(
        normalized_query == normalize_catalog_value(alias)
        for alias in entry.aliases
    )


def _candidate_payload(
    *,
    entity_type: EntityCatalogType,
    entry: EntityCatalogEntry,
    score: float,
) -> dict[str, Any]:
    return {
        "id": entry.id,
        "entity_type": entity_type,
        "name": entry.canonical_value,
        "score": round(score, 2),
    }


def _require_query(value: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError("Entity query must not be empty")

    return normalized


def _normalize(value: str) -> str:
    lowered = value.casefold().strip()
    without_punctuation = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(
        " ",
        without_punctuation,
    ).strip()


def _to_latin_lookalike(value: str) -> str:
    return "".join(
        CYRILLIC_TO_LATIN_HOMOGLYPHS.get(character, character.lower())
        for character in value
    )


def _token_set_ratio(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())

    if not left_tokens or not right_tokens:
        return 0.0

    common = left_tokens & right_tokens
    left_difference = left_tokens - right_tokens
    right_difference = right_tokens - left_tokens

    sorted_common = " ".join(sorted(common))
    sorted_left = (
        f"{sorted_common} {' '.join(sorted(left_difference))}"
    ).strip()
    sorted_right = (
        f"{sorted_common} {' '.join(sorted(right_difference))}"
    ).strip()

    return max(
        SequenceMatcher(
            None,
            sorted_common,
            sorted_left,
        ).ratio(),
        SequenceMatcher(
            None,
            sorted_common,
            sorted_right,
        ).ratio(),
        SequenceMatcher(
            None,
            sorted_left,
            sorted_right,
        ).ratio(),
    ) * 100.0


def _plain_ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio() * 100.0


def _best_token_containment(
    query_norm: str,
    choice_norm: str,
) -> float:
    query_tokens = query_norm.split()
    choice_tokens = choice_norm.split()

    if not query_tokens or not choice_tokens:
        return 0.0

    return max(
        (
            100.0
            if query_token == choice_token
            else SequenceMatcher(
                None,
                query_token,
                choice_token,
            ).ratio()
            * 100.0
        )
        for query_token in query_tokens
        for choice_token in choice_tokens
    )


def _combined_score(query: str, choice: str) -> float:
    query_plain = _normalize(query)
    choice_plain = _normalize(choice)

    if not query_plain or not choice_plain:
        return 0.0

    query_latin = _to_latin_lookalike(query_plain)
    choice_latin = _to_latin_lookalike(choice_plain)

    return max(
        _token_set_ratio(query_plain, choice_plain),
        _plain_ratio(query_plain, choice_plain),
        _token_set_ratio(query_latin, choice_latin),
        _plain_ratio(query_latin, choice_latin),
        _best_token_containment(query_latin, choice_latin),
    )
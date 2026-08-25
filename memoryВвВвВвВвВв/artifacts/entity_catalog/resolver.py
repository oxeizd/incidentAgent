from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.memory.artifacts.catalog.contracts import (
    ENTITY_CATALOG_TYPES,
    EntityCatalogEntry,
    EntityCatalogType,
    EntityLookupCandidate,
    EntityLookupResult,
    normalize_catalog_value,
)
from app.memory.artifacts.catalog.service import EntityCatalogService


_CYRILLIC_TO_LATIN_HOMOGLYPHS = {
    "а": "a",
    "е": "e",
    "к": "k",
    "м": "m",
    "н": "h",
    "о": "o",
    "р": "p",
    "с": "c",
    "т": "t",
    "х": "x",
    "у": "y",
    "в": "b",
}


@dataclass(frozen=True, slots=True)
class EntityResolverSettings:
    matched_threshold: float = 82.0
    ambiguous_gap: float = 6.0
    cross_type_ambiguous_gap: float = 4.0
    cache_ttl_seconds: float = 60.0
    max_candidates: int = 10


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    entity_type: EntityCatalogType
    entry: EntityCatalogEntry
    score: float
    matched_by: str
    matched_value: str


class EntityCatalogResolver:
    """
    In-memory lookup over DB-backed entity catalog.

    Результат всегда один из:
    - matched: можно подставить match.value в structured filter;
    - ambiguous: агент обязан спросить пользователя;
    - not_found: агент не подставляет исходный user text в filter.
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

    async def lookup(
        self,
        *,
        query: str,
        entity_type: EntityCatalogType | None = None,
        limit: int = 5,
    ) -> EntityLookupResult:
        normalized_query = _require_query(query)
        bounded_limit = self._normalize_limit(limit)
        snapshot = await self._snapshot()

        selected_types = (
            (entity_type,)
            if entity_type is not None
            else ENTITY_CATALOG_TYPES
        )

        exact_candidates = _deduplicate_candidates(
            candidate
            for current_type in selected_types
            for entry in snapshot[current_type]
            for candidate in _exact_candidates(
                query=normalized_query,
                entity_type=current_type,
                entry=entry,
            )
        )

        if exact_candidates:
            return self._resolve_exact(
                query=query,
                requested_type=entity_type,
                candidates=exact_candidates,
                limit=bounded_limit,
            )

        scored_candidates = _deduplicate_candidates(
            _score_entry(
                query=normalized_query,
                entity_type=current_type,
                entry=entry,
            )
            for current_type in selected_types
            for entry in snapshot[current_type]
        )

        if not scored_candidates:
            return EntityLookupResult(
                query=query,
                status="not_found",
                entity_type=entity_type,
            )

        return self._resolve_scored(
            query=query,
            requested_type=entity_type,
            candidates=scored_candidates,
            limit=bounded_limit,
        )

    async def refresh(self) -> None:
        """Forces the next lookup to reload data from the database."""
        async with self._cache_lock:
            self._cache_expires_at = 0.0

    def _resolve_exact(
        self,
        *,
        query: str,
        requested_type: EntityCatalogType | None,
        candidates: list[_ScoredCandidate],
        limit: int,
    ) -> EntityLookupResult:
        if len(candidates) == 1:
            match = _candidate_payload(candidates[0])

            return EntityLookupResult(
                query=query,
                status="matched",
                entity_type=match.entity_type,
                match=match,
            )

        return EntityLookupResult(
            query=query,
            status="ambiguous",
            entity_type=requested_type,
            candidates=[
                _candidate_payload(candidate)
                for candidate in candidates[:limit]
            ],
        )

    def _resolve_scored(
        self,
        *,
        query: str,
        requested_type: EntityCatalogType | None,
        candidates: list[_ScoredCandidate],
        limit: int,
    ) -> EntityLookupResult:
        best = candidates[0]

        if best.score < self._settings.matched_threshold:
            return EntityLookupResult(
                query=query,
                status="not_found",
                entity_type=requested_type,
                candidates=[
                    _candidate_payload(candidate)
                    for candidate in candidates[:limit]
                ],
            )

        ambiguous_candidates = [
            candidate
            for candidate in candidates
            if self._is_ambiguous_with_best(
                best=best,
                candidate=candidate,
                requested_type=requested_type,
            )
        ]

        if len(ambiguous_candidates) > 1:
            return EntityLookupResult(
                query=query,
                status="ambiguous",
                entity_type=requested_type,
                candidates=[
                    _candidate_payload(candidate)
                    for candidate in ambiguous_candidates[:limit]
                ],
            )

        match = _candidate_payload(best)

        return EntityLookupResult(
            query=query,
            status="matched",
            entity_type=match.entity_type,
            match=match,
        )

    def _is_ambiguous_with_best(
        self,
        *,
        best: _ScoredCandidate,
        candidate: _ScoredCandidate,
        requested_type: EntityCatalogType | None,
    ) -> bool:
        if candidate is best:
            return True

        if candidate.score < self._settings.matched_threshold:
            return False

        gap = (
            self._settings.ambiguous_gap
            if (
                requested_type is not None
                or candidate.entity_type == best.entity_type
            )
            else self._settings.cross_type_ambiguous_gap
        )

        return best.score - candidate.score <= gap

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

    def _normalize_limit(self, value: int) -> int:
        if value < 1:
            raise ValueError("limit must be at least 1")

        return min(value, self._settings.max_candidates)


def _exact_candidates(
    *,
    query: str,
    entity_type: EntityCatalogType,
    entry: EntityCatalogEntry,
) -> list[_ScoredCandidate]:
    if query == entry.normalized_value:
        return [
            _ScoredCandidate(
                entity_type=entity_type,
                entry=entry,
                score=100.0,
                matched_by="canonical",
                matched_value=entry.canonical_value,
            )
        ]

    return [
        _ScoredCandidate(
            entity_type=entity_type,
            entry=entry,
            score=100.0,
            matched_by="alias",
            matched_value=alias,
        )
        for alias in entry.aliases
        if normalize_catalog_value(alias) == query
    ]


def _score_entry(
    *,
    query: str,
    entity_type: EntityCatalogType,
    entry: EntityCatalogEntry,
) -> _ScoredCandidate:
    variants = (
        ("canonical", entry.canonical_value),
        *(("alias", alias) for alias in entry.aliases),
    )

    score, matched_by, matched_value = max(
        (
            (
                _combined_score(query, value),
                variant_type,
                value,
            )
            for variant_type, value in variants
        ),
        key=lambda item: (
            item[0],
            item[1] == "canonical",
            item[2].casefold(),
        ),
    )

    return _ScoredCandidate(
        entity_type=entity_type,
        entry=entry,
        score=score,
        matched_by=matched_by,
        matched_value=matched_value,
    )


def _deduplicate_candidates(
    values: Iterable[_ScoredCandidate],
) -> list[_ScoredCandidate]:
    unique: dict[tuple[str, str], _ScoredCandidate] = {}

    for item in values:
        key = (
            item.entity_type,
            item.entry.canonical_value,
        )
        previous = unique.get(key)

        if previous is None or _candidate_sort_key(
            item
        ) < _candidate_sort_key(previous):
            unique[key] = item

    return sorted(unique.values(), key=_candidate_sort_key)


def _candidate_sort_key(
    item: _ScoredCandidate,
) -> tuple[float, int, int, str, str]:
    return (
        -item.score,
        0 if item.matched_by == "canonical" else 1,
        -item.entry.source_count,
        item.entity_type,
        item.entry.canonical_value,
    )


def _candidate_payload(
    candidate: _ScoredCandidate,
) -> EntityLookupCandidate:
    return EntityLookupCandidate(
        id=candidate.entry.id,
        entity_type=candidate.entity_type,
        value=candidate.entry.canonical_value,
        score=round(candidate.score, 2),
        matched_by=(
            "canonical"
            if candidate.matched_by == "canonical"
            else "alias"
        ),
        matched_value=candidate.matched_value,
    )


def _require_query(value: str) -> str:
    normalized = normalize_catalog_value(value)

    if not normalized:
        raise ValueError("Entity query must not be empty")

    return normalized


def _to_latin_lookalike(value: str) -> str:
    return "".join(
        _CYRILLIC_TO_LATIN_HOMOGLYPHS.get(character, character)
        for character in value
    )


def _plain_ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio() * 100.0


def _token_set_ratio(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())

    if not left_tokens or not right_tokens:
        return 0.0

    common = left_tokens & right_tokens

    if not common:
        return 0.0

    common_text = " ".join(sorted(common))
    left_text = " ".join(
        (
            common_text,
            *sorted(left_tokens - right_tokens),
        )
    ).strip()
    right_text = " ".join(
        (
            common_text,
            *sorted(right_tokens - left_tokens),
        )
    ).strip()

    return max(
        _plain_ratio(left_text, right_text),
        _plain_ratio(common_text, left_text),
        _plain_ratio(common_text, right_text),
    )


def _subset_score(query: str, choice: str) -> float:
    """
    Partial phrase match cannot return 100.

    This prevents the old failure mode where one shared token caused an
    automatic exact match for unrelated multi-word catalog values.
    """
    query_tokens = set(query.split())
    choice_tokens = set(choice.split())

    if not query_tokens or not choice_tokens:
        return 0.0

    if not query_tokens.issubset(choice_tokens):
        return 0.0

    if query_tokens == choice_tokens:
        return 100.0

    coverage = len(query_tokens) / len(choice_tokens)
    return min(80.0, 60.0 + 20.0 * coverage)


def _combined_score(query: str, choice: str) -> float:
    choice_normalized = normalize_catalog_value(choice)

    if not query or not choice_normalized:
        return 0.0

    query_latin = _to_latin_lookalike(query)
    choice_latin = _to_latin_lookalike(choice_normalized)

    return max(
        _plain_ratio(query, choice_normalized),
        _token_set_ratio(query, choice_normalized),
        _subset_score(query, choice_normalized),
        _plain_ratio(query_latin, choice_latin),
        _token_set_ratio(query_latin, choice_latin),
        _subset_score(query_latin, choice_latin),
    )
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Tuple


logger = logging.getLogger(__name__)


ENTITY_TYPES = ["system_name", "work_group", "executor_name", "element_name", "created_by"]


CYRILLIC_TO_LATIN_HOMOGLYPHS = {
    "а": "a", "А": "a", "е": "e", "Е": "e", "к": "k", "К": "k",
    "м": "m", "М": "m", "н": "h", "Н": "h", "о": "o", "О": "o",
    "р": "p", "Р": "p", "с": "c", "С": "c", "т": "t", "Т": "t",
    "х": "x", "Х": "x", "у": "y", "У": "y", "в": "b", "В": "b",
}


def _to_latin_lookalike(s: str) -> str:
    return "".join(CYRILLIC_TO_LATIN_HOMOGLYPHS.get(ch, ch.lower()) for ch in s)


def _normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _token_set_ratio(a: str, b: str) -> float:
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0.0
    common = a_tokens & b_tokens
    a_diff = a_tokens - b_tokens
    b_diff = b_tokens - a_tokens
    sorted_common = " ".join(sorted(common))
    sorted_a = " ".join(sorted(common)) + " " + " ".join(sorted(a_diff))
    sorted_b = " ".join(sorted(common)) + " " + " ".join(sorted(b_diff))
    ratios = [
        SequenceMatcher(None, sorted_common, sorted_a).ratio(),
        SequenceMatcher(None, sorted_common, sorted_b).ratio(),
        SequenceMatcher(None, sorted_a, sorted_b).ratio(),
    ]
    return max(ratios) * 100


def _plain_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() * 100


def _best_token_containment(query_norm: str, choice_norm: str) -> float:
    query_tokens = query_norm.split()
    choice_tokens = choice_norm.split()
    if not query_tokens or not choice_tokens:
        return 0.0
    best = 0.0
    for qt in query_tokens:
        for ct in choice_tokens:
            score = 100.0 if qt == ct else SequenceMatcher(None, qt, ct).ratio() * 100
            best = max(best, score)
    return best


def _combined_score(query: str, choice: str) -> float:
    q_plain = _normalize(query)
    c_plain = _normalize(choice)
    q_latin = _to_latin_lookalike(q_plain)
    c_latin = _to_latin_lookalike(c_plain)
    scores = [
        _token_set_ratio(q_plain, c_plain),
        _plain_ratio(q_plain, c_plain),
        _token_set_ratio(q_latin, c_latin),
        _plain_ratio(q_latin, c_latin),
        _best_token_containment(q_latin, c_latin),
    ]
    return max(scores)


def extract_matches(query: str, choices: List[str], top_k: int = 5) -> List[Tuple[str, float]]:
    scored = [(choice, _combined_score(query, choice)) for choice in choices]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


class EntityResolver:
    """
    Резолвит неточные/сокращённые имена систем, групп, исполнителей в точные
    канонические значения. Единый источник данных — entity_catalog.json,
    где каждый ключ типа сущности хранит словарь {alias: canonical_name}.
    """

    def __init__(self, catalog_path: str, threshold: int = 65, ambiguous_gap: int = 8, cross_type_gap: int = 10):
        self.catalog_path = Path(catalog_path)
        self.threshold = threshold
        self.ambiguous_gap = ambiguous_gap
        self.cross_type_gap = cross_type_gap
        self.catalog: Dict[str, Dict[str, str]] = {}
        self._exact_cache: Dict[Tuple[str, str], str] = {}
        self._any_cache: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not self.catalog_path.exists():
            logger.warning(f"Файл каталога не найден: {self.catalog_path}")
            self.catalog = {t: {} for t in ENTITY_TYPES}
            return

        try:
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Не удалось прочитать каталог {self.catalog_path}: {e}. Использую пустой каталог.")
            self.catalog = {t: {} for t in ENTITY_TYPES}
            return

        if not isinstance(raw, dict):
            logger.error(f"Каталог {self.catalog_path} имеет неверный формат. Использую пустой каталог.")
            self.catalog = {t: {} for t in ENTITY_TYPES}
            return

        self.catalog = {t: raw.get(t, {}) for t in ENTITY_TYPES}

        extra_types = set(raw) - set(ENTITY_TYPES)
        if extra_types:
            logger.warning(f"В каталоге найдены неизвестные типы сущностей: {sorted(extra_types)}")
            for t in extra_types:
                self.catalog[t] = raw[t]

        logger.info(f"Каталог загружен: {[(k, len(v)) for k, v in self.catalog.items()]}")

    def reload(self):
        """Перечитывает каталог с диска и очищает кеш. Вызывать после переиндексации данных."""
        self._load()
        self._exact_cache.clear()
        self._any_cache.clear()

    def resolve(self, query: str, entity_type: str, top_k: int = 5) -> Dict:
        """Резолв ВНУТРИ ОДНОГО заранее известного типа."""
        if entity_type not in self.catalog:
            return {"status": "not_found", "value": None, "candidates": []}

        cache_key = (query.strip().lower(), entity_type)
        if cache_key in self._exact_cache:
            return {"status": "exact", "value": self._exact_cache[cache_key], "candidates": []}

        entries: Dict[str, str] = self.catalog.get(entity_type, {})
        query_lower = query.strip().lower()
        for alias_key, canonical in entries.items():
            if alias_key.lower() == query_lower:
                self._exact_cache[cache_key] = canonical
                return {"status": "exact", "value": canonical, "candidates": []}

        if not entries:
            return {"status": "not_found", "value": None, "candidates": []}

        choices = list(entries.keys())
        matches = extract_matches(query, choices, top_k=top_k)
        if not matches:
            return {"status": "not_found", "value": None, "candidates": []}

        best_key, best_score = matches[0]
        if best_score < self.threshold:
            return {"status": "not_found", "value": None, "candidates": [(entries[k], s) for k, s in matches]}

        close_matches = [m for m in matches if best_score - m[1] <= self.ambiguous_gap]
        if len(close_matches) > 1:
            return {"status": "ambiguous", "value": None, "candidates": [(entries[k], s) for k, s in close_matches]}

        canonical = entries[best_key]
        self._exact_cache[cache_key] = canonical
        return {"status": "exact", "value": canonical, "candidates": []}

    def resolve_any(self, query: str, top_k: int = 5) -> Dict:
        """
        Не требует entity_type заранее — сам определяет, к какому типу
        относится query, и ищет по ВСЕМ типам сразу.
        """
        query_lower = query.strip().lower()

        cached = self._any_cache.get(query_lower)
        if cached is not None:
            return {**cached, "candidates": []}

        exact_hits: List[Tuple[str, str]] = []
        for entity_type, entries in self.catalog.items():
            for alias_key, canonical in entries.items():
                if alias_key.lower() == query_lower:
                    exact_hits.append((entity_type, canonical))
                    break

        if len(exact_hits) == 1:
            entity_type, canonical = exact_hits[0]
            result = {"status": "exact", "entity_type": entity_type, "value": canonical, "candidates": []}
            self._any_cache[query_lower] = result
            return result
        if len(exact_hits) > 1:
            return {
                "status": "ambiguous", "entity_type": None, "value": None,
                "candidates": [{"entity_type": t, "value": v, "score": 100.0} for t, v in exact_hits],
            }

        all_candidates: List[Tuple[str, str, float]] = []
        for entity_type, entries in self.catalog.items():
            if not entries:
                continue
            matches = extract_matches(query, list(entries.keys()), top_k=top_k)
            for alias_key, score in matches:
                all_candidates.append((entity_type, entries[alias_key], score))

        if not all_candidates:
            return {"status": "not_found", "entity_type": None, "value": None, "candidates": []}

        all_candidates.sort(key=lambda x: x[2], reverse=True)
        best_type, best_value, best_score = all_candidates[0]

        if best_score < self.threshold:
            top = all_candidates[:top_k]
            return {
                "status": "not_found", "entity_type": None, "value": None,
                "candidates": [{"entity_type": t, "value": v, "score": s} for t, v, s in top],
            }

        close_same_type = [c for c in all_candidates if c[0] == best_type and best_score - c[2] <= self.ambiguous_gap]
        close_cross_type = [c for c in all_candidates if c[0] != best_type and best_score - c[2] <= self.cross_type_gap]

        seen = set()
        unique_close = []
        for c in close_same_type + close_cross_type:
            key = (c[0], c[1])
            if key not in seen:
                seen.add(key)
                unique_close.append(c)

        if len(unique_close) > 1:
            return {
                "status": "ambiguous", "entity_type": None, "value": None,
                "candidates": [{"entity_type": t, "value": v, "score": s} for t, v, s in unique_close],
            }

        result = {"status": "exact", "entity_type": best_type, "value": best_value, "candidates": []}
        self._any_cache[query_lower] = result
        return result


# Синглтон по КАЖДОМУ уникальному пути к каталогу, а не глобальный один
# экземпляр. Раньше get_entity_resolver() с одним каталогом молча игнорировала
# catalog_path при повторных вызовах, если резолвер уже был создан с другим
# путём — теперь каждый путь получает свой независимый резолвер и кэш.
_resolver_instances: Dict[str, "EntityResolver"] = {}


def get_entity_resolver(catalog_path: str = "data/entity_catalog.json") -> EntityResolver:
    """Синглтон-геттер резолвера на путь, чтобы каталог не перечитывался с диска на каждый вызов."""
    key = str(Path(catalog_path).resolve())
    if key not in _resolver_instances:
        _resolver_instances[key] = EntityResolver(catalog_path)
    return _resolver_instances[key]


async def lookup_entities(
    query: str,
    top_k: int = 5,
    catalog_path: str = "data/entity_catalog.json",
) -> List[Dict[str, Any]]:
    """
    Найти сущности по запросу (вызывается из nodes_search и search_tools).
    Тип сущности определяется автоматически через resolve_any(); эта функция
    только приводит три возможных статуса резолвера (not_found/ambiguous/exact)
    к единому плоскому контракту List[Dict], который ожидают вызывающие узлы.
    """
    resolver = get_entity_resolver(catalog_path)
    result = resolver.resolve_any(query=query, top_k=top_k)

    if result["status"] == "not_found":
        return []

    if result["status"] == "ambiguous":
        return [
            {
                "id": f"{c['entity_type']}:{c['value']}",
                "entity_type": c["entity_type"],
                "name": c["value"],
                "score": round(c["score"], 2),
            }
            for c in result["candidates"]
        ]

    return [
        {
            "id": f"{result['entity_type']}:{result['value']}",
            "entity_type": result["entity_type"],
            "name": result["value"],
            "score": 100.0,
        }
    ]
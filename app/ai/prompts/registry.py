from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROMPT_REGISTRY: dict[str, str] = {}
DEFAULT_PROMPTS_DIR = Path(__file__).parent / "texts"


def register_prompt(role: str, text: str, *, overwrite: bool = False) -> None:
    if role in PROMPT_REGISTRY and not overwrite:
        raise ValueError(f"prompt for role '{role}' already registered (use overwrite=True to replace)")
    PROMPT_REGISTRY[role] = text.strip()


def get_prompt(role: str, fallback: str = "") -> str:
    return PROMPT_REGISTRY.get(role, fallback)


def load_prompts_from_directory(directory: Optional[Path] = None) -> list[str]:
    directory = directory or DEFAULT_PROMPTS_DIR
    loaded: list[str] = []
    if not directory.exists():
        logger.warning("Prompts directory not found: %s", directory)
        return loaded
    for file in sorted(directory.iterdir()):
        if file.suffix not in (".md", ".txt"):
            continue
        role = file.stem
        register_prompt(role, file.read_text(encoding="utf-8"), overwrite=True)
        loaded.append(role)
    return loaded
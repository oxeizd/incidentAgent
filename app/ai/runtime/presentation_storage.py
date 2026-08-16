"""
app/ai/runtime/presentation_storage.py

Сохраняет готовый HTML презентации на диск сервера, в
data/presentations/<thread_id>/<artifact_id>.html — параллельно с тем, что
презентация кладётся в state["artifacts"] и отдаётся через API. Локальный
файл — надёжный запасной канал: не зависит от того, дошло ли обновление
состояния до клиента через API/фронт, можно найти вручную на сервере.
"""
from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PRESENTATIONS_DIR = Path("data/presentations")


def save_presentation_html(thread_id: str, artifact_id: str, html: str) -> str:
    """Пишет html на диск, возвращает абсолютный путь к файлу (для лога/ответа)."""
    thread_dir = PRESENTATIONS_DIR / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    file_path = thread_dir / f"{artifact_id}.html"
    file_path.write_text(html, encoding="utf-8")
    logger.info("Презентация сохранена локально: %s", file_path)
    return str(file_path.resolve())
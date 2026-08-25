from __future__ import annotations


class MemoryError(RuntimeError):
    """Базовая ошибка memory application layer."""


class ThreadOwnershipError(MemoryError, PermissionError):
    """Тред отсутствует либо принадлежит другому пользователю."""
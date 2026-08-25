from __future__ import annotations


class IncidentReportError(RuntimeError):
    """Базовая контролируемая ошибка RCA report storage."""


class IncidentReportAccessError(IncidentReportError):
    """
    RCA-справка не найдена или недоступна текущему пользователю.

    Наружный API должен возвращать одинаковый 404 для отсутствующего и
    чужого report, не раскрывая факт существования чужих данных.
    """


class IncidentReportVersionConflictError(IncidentReportError):
    """
    Report изменён после формирования editor preview.

    Workflow должен загрузить актуальную версию, не перезаписывая её,
    и попросить пользователя повторно подтвердить новый preview.
    """
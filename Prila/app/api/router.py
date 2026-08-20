from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.presentations import (
    router as presentations_router,
)
from app.api.routes.search_results import (
    router as search_results_router,
)
from app.api.routes.threads import (
    router as threads_router,
)


router = APIRouter()

router.include_router(threads_router)
router.include_router(search_results_router)
router.include_router(presentations_router)
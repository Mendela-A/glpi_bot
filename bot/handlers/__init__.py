from aiogram import Router

from handlers.ticket_form import router as form_router
from handlers.tickets import router as tickets_router
from handlers.followup import router as followup_router
from handlers.common import router as common_router

router = Router()
# Специфічні роутери йдуть перед загальним, щоб fallback не перехоплював їхні хендлери
router.include_router(form_router)
router.include_router(tickets_router)
router.include_router(followup_router)
# common останній — містить fallback_handler без фільтру і error handler
router.include_router(common_router)

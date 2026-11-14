import logging
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API error", exc_info=exc)
    return response

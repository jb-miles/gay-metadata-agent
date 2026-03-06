from __future__ import annotations

import logging

from pythonjsonlogger.jsonlogger import JsonFormatter


def configure_logging(log_level: str) -> None:
    root_logger = logging.getLogger()
    if getattr(configure_logging, "_configured", False):
        root_logger.setLevel(log_level.upper())
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())
    configure_logging._configured = True

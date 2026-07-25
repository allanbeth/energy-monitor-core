from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


ROOT_LOGGER_NAME = "energy_monitor_core"


def configure_logging(app_root: Path, log_level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    if getattr(logger, "_energy_monitor_configured", False):
        return logger

    log_root = Path(app_root) / "logs"
    log_root.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    file_handler = RotatingFileHandler(
        log_root / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.setLevel(log_level)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    logger._energy_monitor_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or ROOT_LOGGER_NAME)


def get_module_logger(app_root: Path, module_name: str) -> logging.Logger:
    logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.module.{module_name}")
    if getattr(logger, "_energy_monitor_module_configured", False):
        return logger

    log_root = Path(app_root) / "logs" / "modules"
    log_root.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    handler = RotatingFileHandler(
        log_root / f"{module_name}.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = True
    logger._energy_monitor_module_configured = True  # type: ignore[attr-defined]
    return logger

from __future__ import annotations

from importlib import import_module
from typing import Any

from ..live_data import SENSOR_DATA_STORE
from ..logging import get_logger
from .base import BaseModulePoller


logger = get_logger(__name__)


def get_module_poller(module_name: str, config_manager: Any, live_data_store: Any = None) -> BaseModulePoller | None:
    normalized = str(module_name or "").strip().lower()
    if not normalized:
        return None

    try:
        module = import_module(f".{normalized}", __name__)
        poller_class = getattr(module, "ModulePoller", None)
        if poller_class is None:
            return None
        return poller_class(normalized, config_manager, live_data_store or SENSOR_DATA_STORE)
    except Exception as error:
        logger.warning("Unable to load module poller for %s: %s", normalized, error)
        return None

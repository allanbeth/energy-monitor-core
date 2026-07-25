from __future__ import annotations

from typing import Any, Dict

from .ina import PROFILE as INA_PROFILE
from .mppt import PROFILE as MPPT_PROFILE
from .victron import PROFILE as VICTRON_PROFILE

PROFILES = {
    INA_PROFILE["name"]: INA_PROFILE,
    VICTRON_PROFILE["name"]: VICTRON_PROFILE,
    MPPT_PROFILE["name"]: MPPT_PROFILE,
}


def get_module_profile(module_name: str) -> Dict[str, Any]:
    return PROFILES.get(str(module_name or "").strip().lower(), {
        "name": str(module_name or "module").strip().lower() or "module",
        "title": str(module_name or "Module").strip().title() or "Module",
        "sensor_types": ["solar", "wind", "battery"],
    })
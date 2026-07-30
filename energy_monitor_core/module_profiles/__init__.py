from __future__ import annotations

from importlib import import_module
import pkgutil
from typing import Any, Dict


def _normalize_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(profile or {})
    name = str(normalized.get("name") or "").strip().lower()
    if not name:
        return {}

    normalized["name"] = name
    normalized.setdefault("title", name.title())
    normalized.setdefault("sensor_types", ["solar", "wind", "battery"])
    return normalized


def _discover_profiles() -> Dict[str, Dict[str, Any]]:
    profiles: Dict[str, Dict[str, Any]] = {}
    for module_info in pkgutil.iter_modules(__path__):
        module_name = str(module_info.name or "").strip()
        if not module_name or module_name.startswith("_"):
            continue

        try:
            module = import_module(f".{module_name}", __name__)
        except Exception:
            continue

        profile = getattr(module, "PROFILE", None)
        if not isinstance(profile, dict):
            continue

        normalized = _normalize_profile(profile)
        if not normalized:
            continue

        profiles[normalized["name"]] = normalized

    return profiles


PROFILES = _discover_profiles()


def get_module_profile(module_name: str) -> Dict[str, Any]:
    return PROFILES.get(str(module_name or "").strip().lower(), {
        "name": str(module_name or "module").strip().lower() or "module",
        "title": str(module_name or "Module").strip().title() or "Module",
        "sensor_types": ["solar", "wind", "battery"],
    })
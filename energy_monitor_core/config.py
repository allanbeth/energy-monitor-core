# isort: skip_file

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth import build_password_hash, verify_password_hash
from .logging import get_logger


logger = get_logger(__name__)


def _slugify_identifier(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    cleaned = "".join(char if (char.isalnum() or char in {"-", "_"}) else "-" for char in text)
    normalized = "-".join(part for part in cleaned.replace("_", "-").split("-") if part)
    return normalized or fallback


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_CORE_CONFIG: dict[str, Any] = {
    "locations": [
        {
            "id": "home",
            "name": "Home",
            "is_default": True,
        }
    ],
    "systems": [
        {
            "id": "home-main",
            "name": "Home Main",
            "location_id": "home",
            "is_default": True,
        }
    ],
    "modules": {
        "ina": {
            "active": True,
            "definitions": {
                "name": "ina",
                "sensor-title": "INA Sensor",
                "settings-title": "INA Settings",
                "settings-description": "INA sensor configuration",
                "icon": "fa-microchip",
                "settings-childPage": "ina-settings",
                "mqtt-defaults": {
                    "sensor-entities": {
                        "base_state_topic": "energy_monitor_core/ina/sensors",
                        "availability_topic_template": "homeassistant/sensor/{sensor_id}/availability",
                    }
                },
            },
            "runtime": {
                "folder": "ina_sensor_monitor",
                "dependency_manifest": "module_dependencies.json",
                "aliases": ["ina"],
            },
        },
        "victron": {
            "active": True,
            "definitions": {
                "name": "victron",
                "sensor-title": "Victron Sensor",
                "settings-title": "Victron Settings",
                "settings-description": "Victron system configuration",
                "icon": "fa-battery-half",
                "settings-childPage": "victron-settings",
                "mqtt-defaults": {
                    "sensor-entities": {
                        "base_state_topic": "energy_monitor_core/victron/sensors",
                        "availability_topic_template": "homeassistant/sensor/{sensor_id}/availability",
                    }
                },
            },
            "runtime": {
                "folder": "victron",
                "dependency_manifest": "module_dependencies.json",
                "aliases": ["victron"],
            },
        },
        "mppt": {
            "active": False,
            "definitions": {
                "name": "mppt",
                "sensor-title": "MPPT Sensor",
                "settings-title": "MPPT Settings",
                "settings-description": "MPPT controller configuration",
                "icon": "fa-solar-panel",
                "settings-childPage": "mppt-settings",
                "mqtt-defaults": {
                    "sensor-entities": {
                        "base_state_topic": "energy_monitor_core/mppt/sensors",
                        "availability_topic_template": "homeassistant/sensor/{sensor_id}/availability",
                    }
                },
            },
            "runtime": {
                "folder": "mppt",
                "dependency_manifest": "module_dependencies.json",
                "aliases": ["mppt"],
            },
        },
    },
    "mqtt": {
        "broker": "localhost",
        "port": 1883,
        "username": "",
        "password": "",
        "client_id": "energy_monitor_core",
        "base_topic": "energy_monitor_core",
        "discovery_prefix": "homeassistant",
        "one_time_broker_sweep": True,
        "one_time_broker_sweep_aggressive": False,
        "enabled": False,
    },
    "webserver": {
        "enabled": True,
        "connection_status": "Disconnected",
        "host": "0.0.0.0",
        "port": 8030,
        "socket_status": "Disconnected",
    },
    "auth": {
        "enabled": True,
        "username": "admin",
        "password_hash": build_password_hash("admin"),
    },
    "logs": {
        "max_size": 10,
    },
    "general": {
        "config_file": "core_config.json",
        "version": "2.0.0",
        "auto_backup_enabled": True,
        "auto_backup_frequency": "Weekly",
        "max_backup_files": 5,
    },
}


DEFAULT_MODULE_FILES: dict[str, dict[str, Any]] = {
    "ina": {
        "config.json": {
            "devices": [],
            "poll_intervals": {"Wind": 7, "Solar": 5, "Battery": 10},
            "max_log": 11,
            "max_readings": 20,
            "strict_variant_mode": False,
            "variant_profile_overrides": {
                "INA219": {"current_lsb": 9.765625e-05},
                "INA226": {"current_lsb": 0.001, "voltage_lsb": 0.00125},
                "INA228": {},
                "INA237": {},
                "INA238": {},
                "INA260": {"current_lsb": 0.00125, "voltage_lsb": 0.00125},
                "INA3221": {"shunt_resistance_ohms": 0.1},
            },
        },
        "sensors.json": [],
        "module_dependencies.json": {
            "module": "ina",
            "python_dependencies": [{"pip": "pigpio", "import": "pigpio", "optional": False}],
        },
    },
    "victron": {
        "config.json": {
            "bluetooth": {"adapter": "hci0", "connection_timeout": 15, "scan_timeout": 10},
            "devices": [],
            "max_log": 10,
            "max_readings": 20,
            "poll_intervals": {"Battery": 6, "Solar": 8, "Wind": 10},
        },
        "sensors.json": [],
        "module_dependencies.json": {
            "module": "victron",
            "system_dependencies": [
                {"apt": "bluez", "command": "bluetoothctl", "optional": False},
                {"apt": "dbus", "command": "dbus-daemon", "optional": False},
            ],
            "python_dependencies": [
                {"pip": "bleak", "import": "bleak", "optional": False},
                {"pip": "pycryptodome", "import": "Crypto", "optional": True},
            ],
        },
    },
    "mppt": {
        "config.json": {
            "devices": [],
            "poll_intervals": {"Solar": 8, "Battery": 8, "Charger": 8},
            "max_log": 10,
            "max_readings": 20,
            "monitoring": {
                "logging_enabled": True,
                "log_interval": 60,
                "alerts_enabled": True,
                "overvoltage_threshold": 16.0,
                "undervoltage_threshold": 10.5,
            },
        },
        "sensors.json": [],
        "module_dependencies.json": {
            "module": "mppt",
            "python_dependencies": [
                {"pip": "pymodbus", "import": "pymodbus", "optional": False},
                {"pip": "pyserial", "import": "serial", "optional": False},
            ],
        },
    },
}


DEFAULT_CORE_MQTT_REGISTRY: dict[str, Any] = {}


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


class ConfigManager:
    def __init__(self, app_root: Path):
        self.app_root = Path(app_root).resolve()
        self.core_dir = self.app_root / "monitor_core"
        self.core_config_path = self.core_dir / "core_config.json"
        self.mqtt_registry_path = self.core_dir / "mqtt_entity_registry.json"
        self.module_roots = {
            "ina": self.app_root / "ina_sensor_monitor",
            "victron": self.app_root / "victron",
            "mppt": self.app_root / "mppt",
        }
        self.config = self._bootstrap_config()

    def _bootstrap_config(self) -> dict[str, Any]:
        self.core_dir.mkdir(parents=True, exist_ok=True)
        config = _read_json(self.core_config_path, DEFAULT_CORE_CONFIG)
        config = self._normalize_config(config)
        _write_json(self.core_config_path, config)

        if not self.mqtt_registry_path.exists():
            _write_json(self.mqtt_registry_path, DEFAULT_CORE_MQTT_REGISTRY)

        for module_name, module_assets in DEFAULT_MODULE_FILES.items():
            module_root = self.module_roots[module_name]
            module_root.mkdir(parents=True, exist_ok=True)
            for filename, default_payload in module_assets.items():
                file_path = module_root / filename
                if not file_path.exists():
                    _write_json(file_path, default_payload)

            (module_root / "backups").mkdir(parents=True, exist_ok=True)

        (self.core_dir / "backups").mkdir(parents=True, exist_ok=True)
        return config

    def _normalize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(DEFAULT_CORE_CONFIG)
        if isinstance(config, dict):
            normalized = _deep_merge(normalized, config)

        normalized["locations"] = self._normalize_locations(normalized.get("locations"))
        normalized["systems"] = self._normalize_systems(normalized.get("systems"), normalized["locations"])

        if not isinstance(normalized.get("modules"), dict):
            normalized["modules"] = deepcopy(DEFAULT_CORE_CONFIG["modules"])

        for module_name, module_data in normalized["modules"].items():
            module_data.setdefault("active", False)
            module_data.setdefault("definitions", {})
            module_data.setdefault("runtime", {})
            module_data["runtime"].setdefault("folder", module_name if module_name != "ina" else "ina_sensor_monitor")
            module_data["runtime"].setdefault("dependency_manifest", "module_dependencies.json")
            module_data["runtime"].setdefault("aliases", [module_name])

        normalized.setdefault("webserver", {})
        normalized["webserver"].setdefault("host", "0.0.0.0")
        normalized["webserver"].setdefault("port", 8030)
        normalized["webserver"].setdefault("enabled", True)

        normalized.setdefault("auth", {})
        normalized["auth"].setdefault("enabled", True)
        normalized["auth"].setdefault("username", "admin")
        normalized["auth"].setdefault("password_hash", build_password_hash("admin"))

        normalized.setdefault("general", {})
        normalized["general"].setdefault("config_file", "core_config.json")
        normalized["general"].setdefault("version", "2.0.0")
        normalized["general"].setdefault("auto_backup_enabled", True)
        normalized["general"].setdefault("auto_backup_frequency", "Weekly")
        normalized["general"].setdefault("max_backup_files", 5)

        normalized.setdefault("logs", {})
        normalized["logs"].setdefault("max_size", 10)

        normalized.setdefault("mqtt", {})
        normalized["mqtt"].setdefault("enabled", False)
        normalized["mqtt"].setdefault("base_topic", "energy_monitor_core")
        normalized["mqtt"].setdefault("discovery_prefix", "homeassistant")
        normalized["mqtt"].setdefault("client_id", "energy_monitor_core")
        normalized["mqtt"].setdefault("port", 1883)
        normalized["mqtt"].setdefault("broker", "localhost")

        return normalized

    def _normalize_locations(self, locations: Any) -> list[dict[str, Any]]:
        normalized_locations: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        source = locations if isinstance(locations, list) else []
        for index, location in enumerate(source):
            if not isinstance(location, dict):
                continue
            location_id = _slugify_identifier(location.get("id") or location.get("name"), f"location-{index + 1}")
            if location_id in seen_ids:
                continue
            seen_ids.add(location_id)
            location_name = str(location.get("name") or location_id.replace("-", " ").title()).strip() or location_id.replace("-", " ").title()
            normalized_locations.append({
                "id": location_id,
                "name": location_name,
                "is_default": _as_bool(location.get("is_default")),
            })

        if not normalized_locations:
            normalized_locations = deepcopy(DEFAULT_CORE_CONFIG["locations"])

        if not any(_as_bool(location.get("is_default")) for location in normalized_locations):
            normalized_locations[0]["is_default"] = True

        default_assigned = False
        for location in normalized_locations:
            if _as_bool(location.get("is_default")) and not default_assigned:
                location["is_default"] = True
                default_assigned = True
            else:
                location["is_default"] = False
        return normalized_locations

    def _normalize_systems(self, systems: Any, locations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        location_ids = {str(location.get("id") or "") for location in locations}
        default_location_id = next((str(location.get("id") or "") for location in locations if _as_bool(location.get("is_default"))), "home")

        normalized_systems: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        source = systems if isinstance(systems, list) else []
        for index, system in enumerate(source):
            if not isinstance(system, dict):
                continue
            system_id = _slugify_identifier(system.get("id") or system.get("name"), f"system-{index + 1}")
            if system_id in seen_ids:
                continue
            seen_ids.add(system_id)

            location_id = _slugify_identifier(system.get("location_id"), default_location_id)
            if location_id not in location_ids:
                location_id = default_location_id

            system_name = str(system.get("name") or system_id.replace("-", " ").title()).strip() or system_id.replace("-", " ").title()
            normalized_systems.append({
                "id": system_id,
                "name": system_name,
                "location_id": location_id,
                "is_default": _as_bool(system.get("is_default")),
            })

        if not normalized_systems:
            normalized_systems = deepcopy(DEFAULT_CORE_CONFIG["systems"])

        if not any(_as_bool(system.get("is_default")) for system in normalized_systems):
            normalized_systems[0]["is_default"] = True

        default_assigned = False
        for system in normalized_systems:
            if _as_bool(system.get("is_default")) and not default_assigned:
                system["is_default"] = True
                default_assigned = True
            else:
                system["is_default"] = False
        return normalized_systems

    def reload(self) -> dict[str, Any]:
        self.config = self._normalize_config(_read_json(self.core_config_path, DEFAULT_CORE_CONFIG))
        return self.config

    def save_config(self) -> bool:
        _write_json(self.core_config_path, self.config)
        return True

    def get_config(self) -> dict[str, Any]:
        return deepcopy(self.config)

    def get_module_names(self) -> list[str]:
        return list((self.config.get("modules") or {}).keys())

    def get_module_entry(self, module_name: str) -> dict[str, Any]:
        return deepcopy((self.config.get("modules") or {}).get(module_name, {}))

    def get_module_paths(self, module_name: str) -> dict[str, Path]:
        module_entry = self.config.get("modules", {}).get(module_name, {})
        runtime = module_entry.get("runtime", {}) if isinstance(module_entry, dict) else {}
        folder = runtime.get("folder", module_name)
        module_root = self.app_root / folder
        return {
            "root": module_root,
            "config": module_root / "config.json",
            "sensors": module_root / "sensors.json",
            "dependencies": module_root / "module_dependencies.json",
            "backups": module_root / "backups",
        }

    def get_location_definitions(self) -> list[dict[str, Any]]:
        return deepcopy(self.config.get("locations", [])) if isinstance(self.config.get("locations"), list) else []

    def get_system_definitions(self) -> list[dict[str, Any]]:
        locations = self.get_location_definitions()
        location_names = {str(location.get("id") or ""): str(location.get("name") or "") for location in locations}
        systems = deepcopy(self.config.get("systems", [])) if isinstance(self.config.get("systems"), list) else []
        for system in systems:
            if not isinstance(system, dict):
                continue
            location_id = str(system.get("location_id") or "")
            system["location_name"] = location_names.get(location_id, location_id)
        return systems

    def get_default_system_id(self) -> str:
        systems = self.get_system_definitions()
        default_system = next((system for system in systems if _as_bool(system.get("is_default"))), None)
        if isinstance(default_system, dict):
            return str(default_system.get("id") or "")
        if systems and isinstance(systems[0], dict):
            return str(systems[0].get("id") or "")
        return "home-main"

    def _module_backup_dir(self, module_name: str) -> Path:
        return self.get_module_paths(module_name)["backups"]

    def _core_backup_dir(self) -> Path:
        return self.core_dir / "backups"

    def _new_backup_dir(self, base: Path) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        candidate = base / stamp
        suffix = 1
        while candidate.exists():
            candidate = base / f"{stamp}_{suffix}"
            suffix += 1
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _copy_json_files(self, source_files: list[Path], destination: Path) -> list[str]:
        copied: list[str] = []
        for file_path in source_files:
            if file_path.exists() and file_path.suffix.lower() == ".json":
                shutil.copy2(file_path, destination / file_path.name)
                copied.append(file_path.name)
        return copied

    def enforce_backup_limit(self, backup_root: Path, max_files: int | None = None) -> list[str]:
        limit = int(max_files if max_files is not None else self.config.get("general", {}).get("max_backup_files", 5))
        if limit <= 0 or not backup_root.exists():
            return []

        entries = sorted([item for item in backup_root.iterdir() if item.is_dir()], key=lambda item: item.stat().st_mtime, reverse=True)
        removed: list[str] = []
        for stale in entries[limit:]:
            shutil.rmtree(stale, ignore_errors=True)
            removed.append(stale.name)
        return removed

    def create_core_config_backup(self) -> dict[str, Any]:
        backup_dir = self._new_backup_dir(self._core_backup_dir())
        copied = self._copy_json_files([self.core_config_path, self.mqtt_registry_path], backup_dir)
        removed = self.enforce_backup_limit(self._core_backup_dir())
        return {
            "name": backup_dir.name,
            "path": str(backup_dir),
            "files": copied,
            "removed_old_backups": removed,
            "created_at": backup_dir.name,
        }

    def create_module_backup(self, module_name: str) -> dict[str, Any]:
        paths = self.get_module_paths(module_name)
        backup_dir = self._new_backup_dir(paths["backups"])
        copied = self._copy_json_files([paths["config"], paths["sensors"], paths["dependencies"]], backup_dir)
        removed = self.enforce_backup_limit(paths["backups"])
        return {
            "name": backup_dir.name,
            "path": str(backup_dir),
            "files": copied,
            "removed_old_backups": removed,
            "created_at": backup_dir.name,
            "module": module_name,
        }

    def list_backups(self, module_name: str | None = None) -> list[dict[str, Any]]:
        root = self._core_backup_dir() if module_name in (None, "core") else self._module_backup_dir(module_name)
        if not root.exists():
            return []

        backups: list[dict[str, Any]] = []
        entries: list[Path] = []
        for item in root.iterdir():
            try:
                if item.is_dir():
                    entries.append(item)
            except FileNotFoundError:
                continue

        for entry in sorted(entries, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
            if not entry.exists():
                continue

            try:
                files = sorted([item.name for item in entry.iterdir() if item.is_file() and item.suffix.lower() == ".json"])
            except FileNotFoundError:
                continue
            backups.append({
                "name": entry.name,
                "path": str(entry),
                "files": files,
                "created_at": entry.name,
            })
        return backups

    def restore_core_backup(self, backup_name: str) -> bool:
        backup_dir = self._core_backup_dir() / backup_name
        if not backup_dir.exists():
            return False

        for source in backup_dir.iterdir():
            if source.is_file() and source.suffix.lower() == ".json":
                shutil.copy2(source, self.core_dir / source.name)
        self.reload()
        return True

    def restore_module_backup(self, module_name: str, backup_name: str) -> bool:
        backup_dir = self._module_backup_dir(module_name) / backup_name
        if not backup_dir.exists():
            return False

        paths = self.get_module_paths(module_name)
        for source in backup_dir.iterdir():
            if source.is_file() and source.suffix.lower() == ".json":
                shutil.copy2(source, paths["root"] / source.name)
        return True

    def get_module_payload(self, module_name: str) -> dict[str, Any]:
        paths = self.get_module_paths(module_name)
        module_config = _read_json(paths["config"], DEFAULT_MODULE_FILES[module_name]["config.json"])
        sensor_config = _read_json(paths["sensors"], DEFAULT_MODULE_FILES[module_name]["sensors.json"])
        dependency_manifest = _read_json(paths["dependencies"], DEFAULT_MODULE_FILES[module_name]["module_dependencies.json"])

        return {
            "module": module_name,
            "active": bool((self.config.get("modules", {}).get(module_name, {}) or {}).get("active", False)),
            "definitions": deepcopy((self.config.get("modules", {}).get(module_name, {}) or {}).get("definitions", {})),
            "runtime": deepcopy((self.config.get("modules", {}).get(module_name, {}) or {}).get("runtime", {})),
            "module_config": module_config,
            "sensor_config": sensor_config,
            "dependency_manifest": dependency_manifest,
            "backups": self.list_backups(module_name),
        }

    def get_modules(self) -> dict[str, dict[str, Any]]:
        return {module_name: self.get_module_payload(module_name) for module_name in self.get_module_names()}

    def get_active_modules(self) -> dict[str, dict[str, Any]]:
        return {module_name: module for module_name, module in self.get_modules().items() if module.get("active")}

    def set_module_status(self, module_name: str, active: bool) -> bool:
        modules = self.config.setdefault("modules", {})
        module_entry = modules.setdefault(module_name, {})
        module_entry["active"] = bool(active)
        self.save_config()
        return True

    def update_core_config(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False

        self.create_core_config_backup()
        self.config = self._normalize_config(_deep_merge(self.config, payload))
        return self.save_config()

    def update_module_config(self, module_name: str, module_config: dict[str, Any] | None = None, sensor_config: Any | None = None) -> bool:
        if module_name not in self.get_module_names():
            return False

        self.create_module_backup(module_name)
        paths = self.get_module_paths(module_name)
        if module_config is not None:
            _write_json(paths["config"], module_config)
        if sensor_config is not None:
            _write_json(paths["sensors"], sensor_config)
        return True

    def get_auth_public_config(self) -> dict[str, Any]:
        auth = self.config.get("auth", {}) if isinstance(self.config, dict) else {}
        return {"enabled": bool(auth.get("enabled", True)), "username": str(auth.get("username", "admin") or "admin")}

    def verify_auth_credentials(self, username: str, password: str) -> bool:
        auth = self.config.get("auth", {}) if isinstance(self.config, dict) else {}
        if not bool(auth.get("enabled", True)):
            return True
        expected_username = str(auth.get("username", "admin") or "admin")
        stored_hash = str(auth.get("password_hash") or "")
        if not username or username != expected_username:
            return False
        if not stored_hash:
            return False
        return verify_password_hash(stored_hash, str(password or ""))

    def verify_auth_password(self, password: str) -> bool:
        auth = self.config.get("auth", {}) if isinstance(self.config, dict) else {}
        return verify_password_hash(str(auth.get("password_hash") or ""), str(password or ""))

    def update_auth_credentials(self, new_username: str, new_password: str, current_password: str) -> dict[str, Any]:
        username = str(new_username or "").strip()
        password = str(new_password or "")
        current = str(current_password or "")
        if not username:
            return {"ok": False, "message": "New username is required"}
        if not password:
            return {"ok": False, "message": "New password is required"}
        if not current:
            return {"ok": False, "message": "Current password is required"}
        if not self.verify_auth_password(current):
            return {"ok": False, "message": "Current password is incorrect"}

        self.config.setdefault("auth", {})["enabled"] = True
        self.config["auth"]["username"] = username
        self.config["auth"]["password_hash"] = build_password_hash(password)
        self.save_config()
        return {"ok": True, "message": "Credentials updated"}

    def get_public_status(self, runtime_manager: Any | None = None) -> dict[str, Any]:
        active_modules = self.get_active_modules()
        live_data = runtime_manager.get_full_live_data() if runtime_manager else {}
        aggregate = runtime_manager.get_aggregate_totals() if runtime_manager else {}
        sensor_type_summary = runtime_manager.get_dashboard_sensor_type_summary() if runtime_manager else {}
        dashboard_trends = runtime_manager.get_aggregate_trends() if runtime_manager and hasattr(runtime_manager, "get_aggregate_trends") else {}
        return {
            "auth": self.get_auth_public_config(),
            "general": deepcopy(self.config.get("general", {})),
            "locations": self.get_location_definitions(),
            "systems": self.get_system_definitions(),
            "default_system_id": self.get_default_system_id(),
            "webserver": deepcopy(self.config.get("webserver", {})),
            "mqtt": deepcopy(self.config.get("mqtt", {})),
            "modules": {
                module_name: {
                    "active": module_data.get("active", False),
                    "definitions": module_data.get("definitions", {}),
                    "runtime": module_data.get("runtime", {}),
                }
                for module_name, module_data in self.get_modules().items()
            },
            "active_modules": {
                module_name: {
                    "active": module_data.get("active", False),
                    "definitions": module_data.get("definitions", {}),
                    "runtime": module_data.get("runtime", {}),
                }
                for module_name, module_data in active_modules.items()
            },
            "active_module_count": len(active_modules),
            "sensor_type_summary": sensor_type_summary,
            "dashboard_trends": dashboard_trends,
            "live_data": live_data,
            "aggregate_totals": aggregate,
        }

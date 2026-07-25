from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..live_data import SENSOR_DATA_STORE, normalize_sensor_type


@dataclass
class BaseModulePoller:
    module_name: str
    config_manager: Any
    live_data_store: Any = SENSOR_DATA_STORE

    def get_poll_interval(self) -> int:
        payload = self.config_manager.get_module_payload(self.module_name)
        module_config = payload.get("module_config", {}) if isinstance(payload, dict) else {}
        intervals = module_config.get("poll_intervals", {}) if isinstance(module_config, dict) else {}
        if isinstance(intervals, dict):
            values = [int(value) for value in intervals.values() if str(value).isdigit() or isinstance(value, (int, float))]
            if values:
                return max(5, min(values))
        return 10

    def should_poll_sensor(self, sensor: dict[str, Any], due_sensor_types: set[str] | None) -> bool:
        if not due_sensor_types:
            return True
        return normalize_sensor_type(sensor.get("type")) in due_sensor_types

    def build_snapshot(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        module_payload = payload if isinstance(payload, dict) else self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {}) if isinstance(module_payload, dict) else {}
        sensor_config = module_payload.get("sensor_config", []) if isinstance(module_payload, dict) else []
        return self.live_data_store.build_module_snapshot(
            self.module_name,
            sensor_config,
            module_config=module_config,
            active=bool(module_payload.get("active", False)),
            poll_interval=self.get_poll_interval(),
            definition=module_payload.get("definitions", {}),
            backups=module_payload.get("backups", []),
        )

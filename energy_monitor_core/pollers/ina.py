from __future__ import annotations

from typing import Any

from .base import BaseModulePoller

try:
    import pigpio
except Exception:  # pragma: no cover - optional runtime dependency
    pigpio = None


class ModulePoller(BaseModulePoller):
    def _device_map(self, module_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        devices = module_config.get("devices", []) if isinstance(module_config, dict) else []
        return {str(device.get("id")): device for device in devices if isinstance(device, dict) and device.get("id") is not None}

    def _connect(self, device: dict[str, Any]) -> Any:
        if pigpio is None:
            return None

        try:
            host = str(device.get("gpio_address") or "").strip()
            remote_gpio = bool(device.get("remote_gpio"))
            return pigpio.pi(host) if remote_gpio and host else pigpio.pi()
        except Exception:
            return None

    def _probe_sensor(self, pi: Any, address: Any) -> bool:
        if not pi or not getattr(pi, "connected", False):
            return False

        try:
            address_int = int(address)
        except Exception:
            return False

        handle = None
        try:
            handle = pi.i2c_open(1, address_int)
            pi.i2c_read_byte(handle)
            return True
        except Exception:
            return False
        finally:
            if handle is not None:
                try:
                    pi.i2c_close(handle)
                except Exception:
                    pass

    def poll(self, payload: dict[str, Any] | None = None, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        module_payload = payload if isinstance(payload, dict) else self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {}) if isinstance(module_payload, dict) else {}
        sensor_config = module_payload.get("sensor_config", []) if isinstance(module_payload, dict) else []
        devices = self._device_map(module_config)

        for sensor in sensor_config if isinstance(sensor_config, list) else []:
            if not isinstance(sensor, dict):
                continue
            if not self.should_poll_sensor(sensor, due_sensor_types):
                continue
            device = devices.get(str(sensor.get("device_id"))) or {}
            pi = self._connect(device) if device else None
            connected = bool(pi and getattr(pi, "connected", False))
            if connected:
                connected = self._probe_sensor(pi, sensor.get("address"))

            sensor_payload = {
                "name": sensor.get("name"),
                "type": sensor.get("type"),
                "address": sensor.get("address"),
                "device_id": sensor.get("device_id"),
                "variant": sensor.get("variant"),
                "max_power": sensor.get("max_power"),
                "rating": sensor.get("rating"),
                "connected": connected,
                "status": "connected" if connected else "disconnected",
                "source_topic": f"poller://ina/{sensor.get('device_id') or 'device'}",
            }
            self.live_data_store.ingest_sensor(self.module_name, str(sensor.get("name") or sensor.get("address") or "sensor"), sensor_payload)

            if pi is not None and hasattr(pi, "stop"):
                try:
                    pi.stop()
                except Exception:
                    pass

        return self.build_snapshot(module_payload)

from __future__ import annotations

import asyncio
from typing import Any

from .base import BaseModulePoller

try:
    from bleak import BleakClient
except Exception:  # pragma: no cover - optional runtime dependency
    BleakClient = None


class ModulePoller(BaseModulePoller):
    async def _probe_device(self, device: dict[str, Any]) -> bool:
        if BleakClient is None:
            return False

        mac = str(device.get("mac") or "").strip()
        if not mac:
            return False

        timeout = float(device.get("connection_timeout") or 10)
        client = BleakClient(mac, timeout=timeout)
        try:
            await client.connect()
            return bool(client.is_connected)
        except Exception:
            return False
        finally:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass

    def poll(self, payload: dict[str, Any] | None = None, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        module_payload = payload if isinstance(payload, dict) else self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {}) if isinstance(module_payload, dict) else {}
        sensor_config = module_payload.get("sensor_config", []) if isinstance(module_payload, dict) else []
        devices = module_config.get("devices", []) if isinstance(module_config, dict) else []

        for device in devices if isinstance(devices, list) else []:
            if not isinstance(device, dict) or not device.get("enabled", True):
                continue

            connected = False
            try:
                connected = asyncio.run(self._probe_device(device))
            except Exception:
                connected = False

            device_id = str(device.get("id") or device.get("mac") or "victron")
            for sensor in sensor_config if isinstance(sensor_config, list) else []:
                if not isinstance(sensor, dict):
                    continue
                if not self.should_poll_sensor(sensor, due_sensor_types):
                    continue
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
                    "source_topic": f"poller://victron/{device_id}",
                }
                self.live_data_store.ingest_sensor(self.module_name, str(sensor.get("name") or sensor.get("address") or "sensor"), sensor_payload)

        return self.build_snapshot(module_payload)

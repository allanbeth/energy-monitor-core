from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from .base import BaseModulePoller

try:
    from bleak import BleakClient
except Exception:  # pragma: no cover - optional runtime dependency
    BleakClient = None


class ModulePoller(BaseModulePoller):
    def _bluetoothctl_connect(self, mac: str, timeout: float) -> tuple[bool, str]:
        safe_timeout = max(5, int(timeout))
        try:
            pre_info = subprocess.run(
                ["bluetoothctl", "--timeout", "5", "info", mac],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            pre_output = (pre_info.stdout or "") + "\n" + (pre_info.stderr or "")
            if "Paired: no" in pre_output:
                return False, "bluetoothctl-not-paired"

            connect_result = subprocess.run(
                ["bluetoothctl", "--timeout", str(safe_timeout), "connect", mac],
                capture_output=True,
                text=True,
                timeout=safe_timeout + 3,
                check=False,
            )
            info_result = subprocess.run(
                ["bluetoothctl", "--timeout", "5", "info", mac],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except FileNotFoundError:
            return False, "bluetoothctl-not-installed"
        except subprocess.TimeoutExpired:
            return False, "bluetoothctl-timeout"
        except Exception as error:
            return False, f"bluetoothctl-error:{error.__class__.__name__}"

        info_output = (info_result.stdout or "") + "\n" + (info_result.stderr or "")
        connected = "Connected: yes" in info_output
        if connected:
            return True, "connected-via-bluetoothctl"

        connect_output = ((connect_result.stdout or "") + "\n" + (connect_result.stderr or "")).strip()
        if connect_output:
            compact = " ".join(connect_output.split())[:160]
            return False, f"bluetoothctl-connect-failed:{compact}"
        return False, "bluetoothctl-connect-failed"

    async def _probe_device(self, device: dict[str, Any], module_config: dict[str, Any]) -> tuple[bool, str]:
        if BleakClient is None:
            timeout = float(device.get("connection_timeout") or module_config.get("bluetooth", {}).get("connection_timeout") or 10)
            return self._bluetoothctl_connect(str(device.get("mac") or "").strip(), timeout)

        mac = str(device.get("mac") or "").strip()
        if not mac:
            return False, "missing-mac"

        bluetooth_config = module_config.get("bluetooth", {}) if isinstance(module_config, dict) else {}
        timeout = float(device.get("connection_timeout") or bluetooth_config.get("connection_timeout") or 10)
        target = mac

        client = BleakClient(target, timeout=max(1.0, timeout))
        try:
            await asyncio.wait_for(client.connect(), timeout=max(2.0, timeout + 2.0))
            return bool(client.is_connected), "connected" if client.is_connected else "connect-failed"
        except asyncio.TimeoutError:
            return self._bluetoothctl_connect(mac, timeout)
        except Exception as error:
            fallback_connected, fallback_status = self._bluetoothctl_connect(mac, timeout)
            if fallback_connected:
                return True, fallback_status
            return False, f"connect-error:{error.__class__.__name__}|{fallback_status}"
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
            status_detail = "not-attempted"
            try:
                connected, status_detail = asyncio.run(self._probe_device(device, module_config))
            except Exception as error:
                connected = False
                status_detail = f"probe-runtime-error:{error.__class__.__name__}"

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
                    "status_detail": status_detail,
                }
                self.live_data_store.ingest_sensor(self.module_name, str(sensor.get("name") or sensor.get("address") or "sensor"), sensor_payload)

        return self.build_snapshot(module_payload)

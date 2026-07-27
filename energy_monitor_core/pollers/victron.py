from __future__ import annotations

import asyncio
import struct
import subprocess
from typing import Any

from .base import BaseModulePoller

try:
    from bleak import BleakClient
except Exception:  # pragma: no cover - optional runtime dependency
    BleakClient = None


class ModulePoller(BaseModulePoller):
    victron_service = "97580001-ddf1-48be-b73e-182664615d8e"
    alternative_services = [
        "306b0001-b081-4037-83dc-e59fcc3cdfd0",
        "68c10001-b17f-4d3a-a290-34ad6499937c",
    ]
    data_notify_char = "97580006-ddf1-48be-b73e-182664615d8e"

    def _bluetoothctl_state(self, mac: str) -> dict[str, Any]:
        state = {
            "known": False,
            "paired": False,
            "trusted": False,
            "connected": False,
            "has_victron_service": False,
            "rssi": None,
            "output": "",
        }
        try:
            result = subprocess.run(
                ["bluetoothctl", "--timeout", "5", "info", mac],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            return state

        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        lowered = output.lower()
        not_known = "not available" in lowered or ("device" in lowered and "not found" in lowered)
        state["known"] = not not_known
        state["output"] = output

        for line in output.splitlines():
            text = line.strip()
            low = text.lower()
            if low.startswith("paired:"):
                state["paired"] = text.split(":", 1)[1].strip().lower() in {"yes", "true", "1"}
            elif low.startswith("trusted:"):
                state["trusted"] = text.split(":", 1)[1].strip().lower() in {"yes", "true", "1"}
            elif low.startswith("connected:"):
                state["connected"] = text.split(":", 1)[1].strip().lower() in {"yes", "true", "1"}
            elif low.startswith("rssi:"):
                raw = text.split(":", 1)[1].strip().split(" ", 1)[0]
                try:
                    state["rssi"] = int(raw)
                except Exception:
                    pass
            elif text.startswith("UUID:") and any(service in low for service in [self.victron_service, *self.alternative_services]):
                state["has_victron_service"] = True

        return state

    def _paired_ready(self, state: dict[str, Any], device: dict[str, Any]) -> bool:
        return bool(
            (state.get("known") and (state.get("paired") or state.get("trusted") or state.get("connected") or state.get("has_victron_service")))
            or bool(device.get("paired"))
        )

    def _prime_host_connection(self, mac: str, timeout: float) -> None:
        safe_timeout = max(5, int(timeout))
        try:
            command_input = "\n".join([f"trust {mac}", f"connect {mac}", f"info {mac}", "quit", ""])
            subprocess.run(
                ["timeout", str(safe_timeout + 5), "bluetoothctl"],
                input=command_input,
                text=True,
                capture_output=True,
                timeout=safe_timeout + 8,
                check=False,
            )
        except Exception:
            return

    def _parse_live_data(self, raw_data: bytes) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        if not raw_data or len(raw_data) < 8:
            return parsed

        values = struct.unpack("<4H", raw_data[:8])
        values_signed = struct.unpack("<4h", raw_data[:8])
        parsed["sensor_values"] = list(values)

        voltage = None
        voltage_position = -1
        for idx, val in enumerate(values):
            if 1000 <= val <= 2000:
                voltage = round(val / 100.0, 2)
                voltage_position = idx
                break
            if 10000 <= val <= 20000:
                voltage = round(val / 1000.0, 3)
                voltage_position = idx
                break

        current = None
        for idx, val in enumerate(values_signed):
            if idx == voltage_position:
                continue
            if -30000 <= val <= 30000:
                candidate = val / 1000.0
                if abs(candidate) <= 30:
                    current = round(candidate, 3)
                    break
            if -3000 <= val <= 3000:
                candidate = val / 100.0
                if abs(candidate) <= 30:
                    current = round(candidate, 2)
                    break

        power = None
        for idx, val in enumerate(values):
            if idx == voltage_position:
                continue
            if 0 <= val <= 3000:
                power = float(val)
                break
            if 0 <= val <= 30000:
                candidate = val / 10.0
                if candidate <= 3000:
                    power = round(candidate, 1)
                    break

        if isinstance(voltage, (int, float)):
            parsed["voltage_v"] = float(voltage)
        if isinstance(current, (int, float)):
            parsed["current_a"] = float(current)
        if isinstance(power, (int, float)):
            parsed["power_w"] = float(power)
        elif isinstance(voltage, (int, float)) and isinstance(current, (int, float)):
            parsed["power_w"] = round(float(voltage) * abs(float(current)), 1)

        return parsed

    async def _read_gatt_live_data(self, mac: str, timeout: float) -> dict[str, Any]:
        if BleakClient is None:
            return {"connected": False, "status_detail": "bleak-not-installed"}

        self._prime_host_connection(mac, timeout)
        client = BleakClient(mac, timeout=max(1.0, float(timeout)))
        try:
            await asyncio.wait_for(client.connect(), timeout=max(2.0, float(timeout) + 2.0))
            output: dict[str, Any] = {
                "connected": bool(client.is_connected),
                "status_detail": "connected-via-gatt" if client.is_connected else "gatt-connect-failed",
                "method": "gatt",
            }
            if not client.is_connected:
                return output

            try:
                live_data = await client.read_gatt_char(self.data_notify_char)
            except Exception as read_error:
                output["status_detail"] = f"gatt-read-failed:{read_error.__class__.__name__}"
                return output

            parsed = self._parse_live_data(live_data if isinstance(live_data, (bytes, bytearray)) else bytes())
            if parsed:
                output.update(parsed)
                output["status_detail"] = "connected-via-gatt-live"
            return output
        except asyncio.TimeoutError:
            return {"connected": False, "status_detail": "gatt-timeout", "method": "gatt"}
        except Exception as error:
            return {"connected": False, "status_detail": f"gatt-error:{error.__class__.__name__}", "method": "gatt"}
        finally:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass

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
        mac = str(device.get("mac") or "").strip()
        if not mac:
            return False, "missing-mac"

        bluetooth_config = module_config.get("bluetooth", {}) if isinstance(module_config, dict) else {}
        timeout = float(device.get("connection_timeout") or bluetooth_config.get("connection_timeout") or 10)
        if BleakClient is not None:
            telemetry = await self._read_gatt_live_data(mac, timeout)
            if telemetry.get("connected"):
                self._last_probe_data = telemetry
                return True, str(telemetry.get("status_detail") or "connected-via-gatt")

        fallback_connected, fallback_status = self._bluetoothctl_connect(mac, timeout)
        state = self._bluetoothctl_state(mac)
        if fallback_connected or state.get("connected"):
            self._last_probe_data = {
                "connected": True,
                "status_detail": fallback_status if fallback_connected else "connected-via-bluetoothctl-info",
                "method": "bluetoothctl",
                "rssi": state.get("rssi"),
            }
            return True, str(self._last_probe_data.get("status_detail"))

        if self._paired_ready(state, device):
            self._last_probe_data = {
                "connected": True,
                "status_detail": "paired-ready-waiting-telemetry",
                "method": "bluetoothctl",
                "rssi": state.get("rssi"),
                "paired": bool(state.get("paired") or device.get("paired")),
            }
            return True, "paired-ready-waiting-telemetry"

        self._last_probe_data = {
            "connected": False,
            "status_detail": fallback_status,
            "method": "bluetoothctl",
            "rssi": state.get("rssi"),
        }
        return False, fallback_status

    def poll(self, payload: dict[str, Any] | None = None, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        module_payload = payload if isinstance(payload, dict) else self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {}) if isinstance(module_payload, dict) else {}
        sensor_config = module_payload.get("sensor_config", []) if isinstance(module_payload, dict) else []
        devices = module_config.get("devices", []) if isinstance(module_config, dict) else []

        self._last_probe_data = {}

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
                if str(sensor.get("device_id") or "") not in {"", device_id, str(device.get("id") or "")}:
                    continue
                if not self.should_poll_sensor(sensor, due_sensor_types):
                    continue

                probe_data = self._last_probe_data if isinstance(self._last_probe_data, dict) else {}
                voltage = probe_data.get("voltage_v")
                current = probe_data.get("current_a")
                power = probe_data.get("power_w")

                sensor_type = str(sensor.get("type") or "").strip().lower()
                normalized_current = current
                normalized_power = power
                if isinstance(current, (int, float)) and sensor_type == "charger":
                    normalized_current = abs(float(current))
                if isinstance(power, (int, float)) and sensor_type == "charger":
                    normalized_power = abs(float(power))

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
                    "method": probe_data.get("method"),
                    "paired": probe_data.get("paired", device.get("paired", False)),
                    "rssi": probe_data.get("rssi"),
                    "voltage": float(voltage) if isinstance(voltage, (int, float)) else 0.0,
                    "current": float(normalized_current) if isinstance(normalized_current, (int, float)) else 0.0,
                    "watts": float(normalized_power) if isinstance(normalized_power, (int, float)) else 0.0,
                    "power": float(normalized_power) if isinstance(normalized_power, (int, float)) else 0.0,
                    "charging_state": probe_data.get("charging_state") or ("connected" if connected else "disconnected"),
                    "charge_mode": probe_data.get("charge_mode") or ("connected" if connected else "disconnected"),
                }
                self.live_data_store.ingest_sensor(self.module_name, str(sensor.get("name") or sensor.get("address") or "sensor"), sensor_payload)

        return self.build_snapshot(module_payload)

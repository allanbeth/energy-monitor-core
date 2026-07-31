from __future__ import annotations

import asyncio
import struct
import subprocess
from threading import RLock
from typing import Any

from .base import BaseModulePoller
from ..logging import get_logger

try:
    from bleak import BleakClient
except Exception:  # pragma: no cover - optional runtime dependency
    BleakClient = None


logger = get_logger(__name__)


class ModulePoller(BaseModulePoller):
    victron_service = "97580001-ddf1-48be-b73e-182664615d8e"
    alternative_services = [
        "306b0001-b081-4037-83dc-e59fcc3cdfd0",
        "68c10001-b17f-4d3a-a290-34ad6499937c",
    ]
    data_notify_char = "97580006-ddf1-48be-b73e-182664615d8e"

    def __init__(self, module_name: str, config_manager: Any, live_data_store: Any = None):
        super().__init__(module_name, config_manager, live_data_store)
        self._client_lock = RLock()
        self._device_clients: dict[str, Any] = {}
        self._event_loop = asyncio.new_event_loop()

    def _resolve_charger_charge_state(self, latest_device_data: dict[str, Any]) -> tuple[str, str]:
        mode = str(latest_device_data.get("charge_mode") or "").strip().lower()
        charging_state = str(latest_device_data.get("charging_state") or "").strip().lower()
        current = float(latest_device_data.get("current_a") or 0.0)
        voltage = float(latest_device_data.get("voltage_v") or 0.0)

        state_code = latest_device_data.get("state_code_value")
        if isinstance(state_code, (int, float)):
            state_map = {
                0: ("⭕ Off", "off"),
                1: ("🔋 Low Power", "low_power"),
                2: ("⚠️ Fault", "fault"),
                3: ("⚡ Bulk", "bulk"),
                4: ("🔋 Absorption", "absorption"),
                5: ("💡 Float", "float"),
                6: ("😴 Storage", "storage"),
                7: ("⚖️ Equalize", "equalize"),
                8: ("🔄 Inverting", "inverting"),
                9: ("🔌 Power Supply", "power_supply"),
                10: ("🔁 Starting", "starting"),
            }
            code_key = int(state_code)
            if code_key in state_map:
                return state_map[code_key]

        hints = f"{mode} {charging_state}".strip().lower()
        if any(token in hints for token in ("bulk", "absorption", "float", "storage", "equalize", "charging", "starting")):
            if any(token in hints for token in ("absorption", "equalize")):
                return "🔋 Absorption", "absorption"
            if "storage" in hints:
                return "😴 Storage", "storage"
            if "float" in hints:
                return "💡 Float", "float"
            return "⚡ Bulk", "bulk"

        if abs(current) < 0.05:
            if voltage > 13.6:
                return "💡 Float", "float"
            if voltage > 10:
                return "😴 Idle", "idle"
            return "⭕ Off", "off"

        if current < -0.1:
            if voltage <= 12.5:
                return "⚡ Bulk", "bulk"
            if voltage <= 14.4:
                if abs(current) > 5.0:
                    return "⚡ Bulk", "bulk"
                if abs(current) > 1.0:
                    return "🔋 Absorption", "absorption"
                return "💡 Float", "float"
            return "💡 Float", "float"

        if current > 0.1:
            return "🔌 Discharging", "discharge"

        return "😴 Idle", "idle"

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

    def _bluetoothctl_pair_connect(self, mac: str, timeout: float, passcode: str = "") -> tuple[bool, str]:
        safe_timeout = max(8, int(timeout))
        commands = [
            "power on",
            "agent on",
            "default-agent",
            f"scan on",
            f"pair {mac}",
        ]
        # Best-effort support for PIN-based pairing flows.
        if passcode:
            commands.append(passcode)
            commands.append("yes")
        commands.extend([
            f"trust {mac}",
            f"connect {mac}",
            f"info {mac}",
            "scan off",
            "quit",
            "",
        ])

        try:
            subprocess.run(
                ["timeout", str(safe_timeout + 10), "bluetoothctl"],
                input="\n".join(commands),
                text=True,
                capture_output=True,
                timeout=safe_timeout + 12,
                check=False,
            )
            info = subprocess.run(
                ["bluetoothctl", "--timeout", "5", "info", mac],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except FileNotFoundError:
            return False, "bluetoothctl-not-installed"
        except subprocess.TimeoutExpired:
            return False, "bluetoothctl-pair-timeout"
        except Exception as error:
            return False, f"bluetoothctl-pair-error:{error.__class__.__name__}"

        info_output = (info.stdout or "") + "\n" + (info.stderr or "")
        if "Connected: yes" in info_output:
            return True, "connected-via-bluetoothctl-pair"
        if "Paired: yes" in info_output:
            return False, "paired-not-connected"
        return False, "pair-or-connect-failed"

    def reconnect_device(self, device: dict[str, Any], module_config: dict[str, Any]) -> dict[str, Any]:
        mac = str((device or {}).get("mac") or "").strip()
        if not mac:
            return {"connected": False, "status_detail": "missing-mac"}

        bluetooth_config = module_config.get("bluetooth", {}) if isinstance(module_config, dict) else {}
        timeout = float(device.get("connection_timeout") or bluetooth_config.get("connection_timeout") or 10)
        passcode = str(device.get("passcode") or "").strip()

        connected, status_detail = self._bluetoothctl_connect(mac, timeout)
        if not connected and status_detail in {"bluetoothctl-not-paired", "bluetoothctl-connect-failed", "bluetoothctl-connect-failed:"}:
            connected, status_detail = self._bluetoothctl_pair_connect(mac, timeout, passcode=passcode)

        state = self._bluetoothctl_state(mac)
        final_connected = bool(connected or state.get("connected"))
        self._last_probe_data = {
            "connected": final_connected,
            "status_detail": status_detail,
            "method": "bluetoothctl",
            "paired": bool(state.get("paired")),
            "rssi": state.get("rssi"),
        }
        return dict(self._last_probe_data)

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

    def _device_key(self, device: dict[str, Any]) -> str:
        key = str(device.get("id") or "").strip()
        if key:
            return key
        mac = str(device.get("mac") or "").strip().upper()
        if mac:
            return mac
        return "victron-device"

    def _run_async(self, coroutine):
        if self._event_loop.is_closed():
            self._event_loop = asyncio.new_event_loop()
        return self._event_loop.run_until_complete(coroutine)

    async def _disconnect_client(self, client: Any) -> None:
        if client is None:
            return
        try:
            if bool(getattr(client, "is_connected", False)):
                await client.disconnect()
        except Exception:
            pass

    def _close_stale_clients(self, active_keys: set[str]) -> None:
        stale_clients: list[Any] = []
        with self._client_lock:
            stale_keys = [key for key in self._device_clients if key not in active_keys]
            for key in stale_keys:
                stale_clients.append(self._device_clients.pop(key, None))

        for client in stale_clients:
            try:
                self._run_async(self._disconnect_client(client))
            except Exception:
                pass
        if stale_clients:
            logger.info("Victron: cleaned up %d stale BLE client(s)", len(stale_clients))

    def shutdown(self) -> None:
        clients: list[Any]
        with self._client_lock:
            clients = list(self._device_clients.values())
            self._device_clients.clear()

        for client in clients:
            try:
                self._run_async(self._disconnect_client(client))
            except Exception:
                pass

        if clients:
            logger.info("Victron: closed %d BLE client(s) during poller shutdown", len(clients))

        if not self._event_loop.is_closed():
            try:
                self._event_loop.close()
            except Exception:
                pass

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

        state_code = None
        for idx, val in enumerate(values):
            if 0 <= val <= 10:
                state_code = int(val)
                parsed["state_code_value"] = state_code
                parsed["state_code_position"] = idx
                break

        if state_code is not None:
            display_state, mode = self._resolve_charger_charge_state({
                **parsed,
                "charge_mode": state_code,
                "charging_state": state_code,
            })
            parsed["charging_state"] = display_state
            parsed["charge_mode"] = mode
        elif isinstance(voltage, (int, float)) and isinstance(current, (int, float)):
            display_state, mode = self._resolve_charger_charge_state(parsed)
            parsed["charging_state"] = display_state
            parsed["charge_mode"] = mode

        return parsed

    async def _read_gatt_live_data(self, device_key: str, mac: str, timeout: float) -> dict[str, Any]:
        if BleakClient is None:
            return {"connected": False, "status_detail": "bleak-not-installed"}

        with self._client_lock:
            client = self._device_clients.get(device_key)

        had_connected_session = bool(client and bool(getattr(client, "is_connected", False)))

        if client is None:
            self._prime_host_connection(mac, timeout)
            client = BleakClient(mac, timeout=max(1.0, float(timeout)))
            with self._client_lock:
                self._device_clients[device_key] = client
            logger.info("Victron: created BLE client for device %s", device_key)

        try:
            if not bool(getattr(client, "is_connected", False)):
                await asyncio.wait_for(client.connect(), timeout=max(2.0, float(timeout) + 2.0))
                logger.info("Victron: established BLE session for device %s", device_key)
            elif had_connected_session:
                logger.info("Victron: reusing existing BLE session for device %s", device_key)

            session_state = "reused" if had_connected_session and bool(getattr(client, "is_connected", False)) else "connected"
            output: dict[str, Any] = {
                "connected": bool(client.is_connected),
                "status_detail": "connected-via-gatt" if client.is_connected else "gatt-connect-failed",
                "method": "gatt",
                "connection_session": session_state,
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
            logger.warning("Victron: BLE timeout for device %s", device_key)
            with self._client_lock:
                stale_client = self._device_clients.pop(device_key, None)
            if stale_client is not None:
                await self._disconnect_client(stale_client)
            return {"connected": False, "status_detail": "gatt-timeout", "method": "gatt"}
        except Exception as error:
            logger.warning("Victron: BLE error for device %s: %s", device_key, error.__class__.__name__)
            with self._client_lock:
                stale_client = self._device_clients.pop(device_key, None)
            if stale_client is not None:
                await self._disconnect_client(stale_client)
            return {"connected": False, "status_detail": f"gatt-error:{error.__class__.__name__}", "method": "gatt"}

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

    async def _probe_device(self, device_key: str, device: dict[str, Any], module_config: dict[str, Any]) -> tuple[bool, str]:
        mac = str(device.get("mac") or "").strip()
        if not mac:
            return False, "missing-mac"

        bluetooth_config = module_config.get("bluetooth", {}) if isinstance(module_config, dict) else {}
        timeout = float(device.get("connection_timeout") or bluetooth_config.get("connection_timeout") or 10)
        if BleakClient is not None:
            telemetry = await self._read_gatt_live_data(device_key, mac, timeout)
            if telemetry.get("connected"):
                self._last_probe_data = telemetry
                return True, str(telemetry.get("status_detail") or "connected-via-gatt")

        fallback_connected, fallback_status = self._bluetoothctl_connect(mac, timeout)
        state = self._bluetoothctl_state(mac)
        if fallback_connected or state.get("connected"):
            logger.info("Victron: host bluetoothctl reports connected for device %s", device_key)
            self._last_probe_data = {
                "connected": True,
                "status_detail": fallback_status if fallback_connected else "connected-via-bluetoothctl-info",
                "method": "bluetoothctl",
                "connection_session": "host-connected",
                "rssi": state.get("rssi"),
            }
            return True, str(self._last_probe_data.get("status_detail"))

        if self._paired_ready(state, device):
            self._last_probe_data = {
                "connected": False,
                "status_detail": "paired-not-connected",
                "method": "bluetoothctl",
                "rssi": state.get("rssi"),
                "paired": bool(state.get("paired") or device.get("paired")),
            }
            return False, "paired-not-connected"

        self._last_probe_data = {
            "connected": False,
            "status_detail": fallback_status,
            "method": "bluetoothctl",
            "connection_session": "disconnected",
            "rssi": state.get("rssi"),
        }
        return False, fallback_status

    def poll(self, payload: dict[str, Any] | None = None, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        module_payload = payload if isinstance(payload, dict) else self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {}) if isinstance(module_payload, dict) else {}
        sensor_config = module_payload.get("sensor_config", []) if isinstance(module_payload, dict) else []
        devices = module_config.get("devices", []) if isinstance(module_config, dict) else []
        active_device_keys: set[str] = set()

        self._last_probe_data = {}

        for device in devices if isinstance(devices, list) else []:
            if not isinstance(device, dict) or not device.get("enabled", True):
                continue

            device_key = self._device_key(device)
            active_device_keys.add(device_key)
            connected = False
            status_detail = "not-attempted"
            try:
                connected, status_detail = self._run_async(self._probe_device(device_key, device, module_config))
            except Exception as error:
                connected = False
                status_detail = f"probe-runtime-error:{error.__class__.__name__}"

            device_id = str(device.get("id") or device.get("mac") or "victron")
            for sensor in sensor_config if isinstance(sensor_config, list) else []:
                if not isinstance(sensor, dict):
                    continue
                if str(sensor.get("device_id") or "") not in {"", device_id, str(device.get("id") or "")}:
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
                    "device_connected": connected,
                    "connected": connected,
                    "status": "connected" if connected else "disconnected",
                    "source_topic": f"poller://victron/{device_id}",
                    "status_detail": status_detail,
                    "method": probe_data.get("method"),
                    "connection_session": probe_data.get("connection_session"),
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

            self._close_stale_clients(active_device_keys)

        return self.build_snapshot(module_payload)

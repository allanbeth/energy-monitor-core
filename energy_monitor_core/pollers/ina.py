from __future__ import annotations

from threading import RLock
from typing import Any

from .base import BaseModulePoller

try:
    import pigpio
except Exception:  # pragma: no cover - optional runtime dependency
    pigpio = None


DEFAULT_CALIBRATION = 4191
CALIBRATION_REGISTER = 0x05

VARIANT_PROFILES: dict[str, dict[str, Any]] = {
    "INA219": {
        "voltage_reg": 0x02,
        "current_reg": 0x04,
        "voltage_lsb": 0.004,
        "voltage_shift": 3,
        "current_lsb": 3.2 / 32767,
        "requires_calibration": True,
    },
    "INA260": {
        "voltage_reg": 0x02,
        "current_reg": 0x01,
        "voltage_lsb": 0.00125,
        "voltage_shift": 0,
        "current_lsb": 0.00125,
        "requires_calibration": False,
    },
    "INA226": {
        "voltage_reg": 0x02,
        "current_reg": 0x04,
        "voltage_lsb": 0.00125,
        "voltage_shift": 0,
        "current_lsb": 0.001,
        "requires_calibration": True,
    },
}


class ModulePoller(BaseModulePoller):
    def __init__(self, module_name: str, config_manager: Any, live_data_store: Any = None):
        super().__init__(module_name, config_manager, live_data_store)
        self._device_clients: dict[str, Any] = {}
        self._client_lock = RLock()

    def _normalize_variant(self, variant: Any) -> str:
        text = str(variant or "INA219").strip().upper().replace("_", "").replace("-", "")
        if text.startswith("INA"):
            text = text[3:]
        if text in {"219", "226", "260"}:
            return f"INA{text}"
        return "INA219"

    def _coerce_int(self, value: Any, default: int = 0) -> int:
        try:
            if isinstance(value, str):
                text = value.strip().lower()
                if text.startswith("0x"):
                    return int(text, 16)
            return int(value)
        except Exception:
            return default

    def _format_i2c_address(self, address: Any) -> str:
        try:
            if address is None or address == "":
                return ""
            if isinstance(address, str):
                normalized = address.strip().lower()
                if not normalized:
                    return ""
                if normalized.startswith("0x"):
                    parsed = int(normalized, 16)
                elif normalized.isdigit():
                    parsed = int(normalized, 10)
                else:
                    parsed = int(normalized, 16)
            else:
                parsed = int(address)
            return f"0x{parsed:02x}"
        except Exception:
            return str(address or "")

    def _swap_word_bytes(self, value: int) -> int:
        number = int(value) & 0xFFFF
        return ((number & 0xFF) << 8) | ((number >> 8) & 0xFF)

    def _read_register_16_raw(self, pi: Any, handle: Any, register: int) -> int | None:
        try:
            data = pi.i2c_read_word_data(handle, register)
            return self._swap_word_bytes(data)
        except Exception:
            return None

    def _read_register_16_signed(self, pi: Any, handle: Any, register: int) -> int | None:
        raw = self._read_register_16_raw(pi, handle, register)
        if raw is None:
            return None
        if raw & 0x8000:
            raw -= 0x10000
        return raw

    def _read_measurements(self, pi: Any, sensor: dict[str, Any]) -> dict[str, Any]:
        address = self._coerce_int(sensor.get("address"), -1)
        if address < 0:
            return {"connected": False, "status_detail": "invalid-address"}

        variant = self._normalize_variant(sensor.get("variant"))
        profile = VARIANT_PROFILES.get(variant, VARIANT_PROFILES["INA219"])
        calibration_value = self._coerce_int(sensor.get("calibration_value"), DEFAULT_CALIBRATION)

        handle = None
        try:
            handle = pi.i2c_open(1, address)

            if profile.get("requires_calibration"):
                # pigpio writes words as little-endian; swap so device gets big-endian calibration.
                pi.i2c_write_word_data(handle, CALIBRATION_REGISTER, self._swap_word_bytes(calibration_value))

            raw_voltage = self._read_register_16_raw(pi, handle, int(profile["voltage_reg"]))
            raw_current = self._read_register_16_signed(pi, handle, int(profile["current_reg"]))
            if raw_voltage is None or raw_current is None:
                return {"connected": False, "status_detail": "register-read-failed"}

            if raw_voltage == 0 and raw_current == 0:
                return {
                    "connected": True,
                    "status": "connected",
                    "status_detail": "connected-no-data:zero-registers",
                    "voltage": 0.0,
                    "current": 0.0,
                    "watts": 0.0,
                    "power": 0.0,
                }

            voltage_shift = int(profile.get("voltage_shift", 0))
            voltage_lsb = float(profile["voltage_lsb"])
            current_lsb = float(profile["current_lsb"])

            voltage = round((raw_voltage >> voltage_shift) * voltage_lsb, 3)
            current = round(raw_current * current_lsb, 4)
            if str(sensor.get("type") or "").strip().lower() in {"solar", "wind"}:
                current = abs(current)
            power = round(voltage * current, 3)

            if voltage < 0.05 and abs(current) < 0.01:
                return {
                    "connected": True,
                    "status": "connected",
                    "status_detail": "connected-no-data:no-electrical-signal",
                    "voltage": voltage,
                    "current": current,
                    "watts": power,
                    "power": power,
                }

            return {
                "connected": True,
                "status": "connected",
                "status_detail": f"live-read:{variant}",
                "voltage": voltage,
                "current": current,
                "watts": power,
                "power": power,
            }
        except Exception as error:
            return {"connected": False, "status_detail": f"read-error:{error.__class__.__name__}"}
        finally:
            if handle is not None:
                try:
                    pi.i2c_close(handle)
                except Exception:
                    pass

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

    def _disconnect(self, client: Any) -> None:
        if client is None:
            return
        if hasattr(client, "stop"):
            try:
                client.stop()
            except Exception:
                pass

    def _device_key(self, device: dict[str, Any], sensor: dict[str, Any] | None = None) -> str:
        key = str(device.get("id") if isinstance(device, dict) else "").strip()
        if key:
            return key
        if sensor and isinstance(sensor, dict):
            return str(sensor.get("device_id") or "unknown-device").strip() or "unknown-device"
        return "unknown-device"

    def _get_or_create_client(self, device_key: str, device: dict[str, Any]) -> Any:
        with self._client_lock:
            existing = self._device_clients.get(device_key)
            if existing is not None and bool(getattr(existing, "connected", False)):
                return existing
            if existing is not None:
                self._disconnect(existing)
            client = self._connect(device)
            self._device_clients[device_key] = client
            return client

    def _close_stale_clients(self, active_keys: set[str]) -> None:
        with self._client_lock:
            stale_keys = [key for key in self._device_clients.keys() if key not in active_keys]
            for key in stale_keys:
                client = self._device_clients.pop(key, None)
                self._disconnect(client)

    def _reset_client(self, device_key: str) -> None:
        with self._client_lock:
            client = self._device_clients.pop(device_key, None)
        self._disconnect(client)

    def shutdown(self) -> None:
        with self._client_lock:
            for client in self._device_clients.values():
                self._disconnect(client)
            self._device_clients.clear()

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
        active_device_keys: set[str] = set()

        for sensor in sensor_config if isinstance(sensor_config, list) else []:
            if not isinstance(sensor, dict):
                continue
            if not self.should_poll_sensor(sensor, due_sensor_types):
                continue

            device = devices.get(str(sensor.get("device_id"))) or {}
            pi = None
            device_key = self._device_key(device, sensor)
            if device:
                active_device_keys.add(device_key)
                pi = self._get_or_create_client(device_key, device)

            measurement = {"connected": False, "status": "disconnected", "status_detail": "device-unavailable"}
            connected = bool(pi and getattr(pi, "connected", False))
            if connected:
                measurement = self._read_measurements(pi, sensor)
                connected = bool(measurement.get("connected", False))
                if not connected:
                    address = self._coerce_int(sensor.get("address"), -1)
                    sensor_present = self._probe_sensor(pi, address) if address >= 0 else False
                    if sensor_present:
                        connected = True
                        measurement = {
                            "connected": True,
                            "status": "connected",
                            "status_detail": "connected-no-data:transient-read-failure",
                            "voltage": 0.0,
                            "current": 0.0,
                            "watts": 0.0,
                            "power": 0.0,
                        }
                    else:
                        # Reset the cached pigpio session so the next cycle establishes a fresh connection.
                        self._reset_client(device_key)
                elif str(measurement.get("status_detail") or "").startswith("connected-no-data"):
                    # Keep a no-data read from pinning a potentially stale session forever.
                    self._reset_client(device_key)

            formatted_address = self._format_i2c_address(sensor.get("address"))

            sensor_payload = {
                "name": sensor.get("name"),
                "type": sensor.get("type"),
                "address": formatted_address,
                "device_id": sensor.get("device_id"),
                "variant": sensor.get("variant"),
                "max_power": sensor.get("max_power"),
                "rating": sensor.get("rating"),
                "device_connected": bool(pi and getattr(pi, "connected", False)),
                "connected": connected,
                "status": "connected" if connected else "disconnected",
                "status_detail": measurement.get("status_detail", ""),
                "voltage": measurement.get("voltage", 0.0),
                "current": measurement.get("current", 0.0),
                "watts": measurement.get("watts", 0.0),
                "power": measurement.get("power", 0.0),
                "source_topic": f"poller://ina/{sensor.get('device_id') or 'device'}",
            }
            self.live_data_store.ingest_sensor(self.module_name, str(sensor.get("name") or sensor.get("address") or "sensor"), sensor_payload)

        self._close_stale_clients(active_device_keys)

        return self.build_snapshot(module_payload)

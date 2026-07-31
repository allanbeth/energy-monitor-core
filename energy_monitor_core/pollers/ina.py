from __future__ import annotations

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
                return {"connected": False, "status_detail": "zero-registers"}

            voltage_shift = int(profile.get("voltage_shift", 0))
            voltage_lsb = float(profile["voltage_lsb"])
            current_lsb = float(profile["current_lsb"])

            voltage = round((raw_voltage >> voltage_shift) * voltage_lsb, 3)
            current = round(raw_current * current_lsb, 4)
            if str(sensor.get("type") or "").strip().lower() in {"solar", "wind"}:
                current = abs(current)
            power = round(voltage * current, 3)

            if voltage < 0.05 and abs(current) < 0.01:
                return {"connected": False, "status_detail": "no-electrical-signal"}

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
        device_clients: dict[str, Any] = {}

        try:
            for sensor in sensor_config if isinstance(sensor_config, list) else []:
                if not isinstance(sensor, dict):
                    continue
                if not self.should_poll_sensor(sensor, due_sensor_types):
                    continue

                device = devices.get(str(sensor.get("device_id"))) or {}
                pi = None
                if device:
                    device_key = str(device.get("id", sensor.get("device_id")))
                    if device_key not in device_clients:
                        device_clients[device_key] = self._connect(device)
                    pi = device_clients.get(device_key)

                measurement = {"connected": False, "status": "disconnected", "status_detail": "device-unavailable"}
                connected = bool(pi and getattr(pi, "connected", False))
                if connected:
                    measurement = self._read_measurements(pi, sensor)
                    connected = bool(measurement.get("connected", False))

                formatted_address = self._format_i2c_address(sensor.get("address"))

                sensor_payload = {
                    "name": sensor.get("name"),
                    "type": sensor.get("type"),
                    "address": formatted_address,
                    "device_id": sensor.get("device_id"),
                    "variant": sensor.get("variant"),
                    "max_power": sensor.get("max_power"),
                    "rating": sensor.get("rating"),
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
        finally:
            for client in device_clients.values():
                if client is not None and hasattr(client, "stop"):
                    try:
                        client.stop()
                    except Exception:
                        pass

        return self.build_snapshot(module_payload)

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
        "calibration_reg": 0x05,
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
        "shunt_reg": 0x01,
        "current_fallback_from_shunt": True,
        "voltage_lsb": 0.00125,
        "voltage_shift": 0,
        "current_lsb": 0.001,
        "shunt_lsb": 0.0000025,
        "shunt_resistance_ohms": 0.1,
        "calibration_reg": 0x05,
        "requires_calibration": True,
    },
    "INA3221": {
        "voltage_reg": 0x02,
        "voltage_lsb": 0.008,
        "voltage_shift": 3,
        "current_from_shunt": True,
        "shunt_reg": 0x01,
        "shunt_lsb": 0.00004,
        "shunt_shift": 3,
        "shunt_resistance_ohms": 0.1,
        "requires_calibration": False,
    },
    "INA228": {
        "voltage_reg": 0x05,
        "current_reg": 0x07,
        "voltage_lsb": 0.0001953125,
        "voltage_shift": 0,
        "current_lsb": 0.001,
        "requires_calibration": False,
    },
    "INA237": {
        "voltage_reg": 0x05,
        "current_reg": 0x07,
        "voltage_lsb": 0.003125,
        "voltage_shift": 0,
        "current_lsb": 0.001,
        "requires_calibration": False,
    },
    "INA238": {
        "voltage_reg": 0x05,
        "current_reg": 0x07,
        "voltage_lsb": 0.003125,
        "voltage_shift": 0,
        "current_lsb": 0.001,
        "requires_calibration": False,
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
        if text in {"219", "226", "228", "237", "238", "260", "3221"}:
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

    def _coerce_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _estimate_battery_soc(self, voltage: float, rating: float) -> int:
        # Piecewise-linear approximation carried over from legacy INA behavior.
        if float(rating) >= 20.0:
            soc_table = [
                (25.4, 100),
                (25.2, 90),
                (24.8, 75),
                (24.4, 50),
                (24.0, 25),
                (23.8, 10),
                (23.6, 0),
            ]
            min_v, max_v = 23.6, 25.4
        else:
            soc_table = [
                (12.7, 100),
                (12.6, 90),
                (12.4, 75),
                (12.2, 50),
                (12.0, 25),
                (11.9, 10),
                (11.8, 0),
            ]
            min_v, max_v = 11.8, 12.7

        clamped_voltage = max(min_v, min(max_v, float(voltage)))
        for index in range(len(soc_table) - 1):
            v_high, soc_high = soc_table[index]
            v_low, soc_low = soc_table[index + 1]
            if v_low <= clamped_voltage <= v_high:
                if v_high == v_low:
                    return int(round(soc_low))
                soc_value = soc_low + (soc_high - soc_low) * (clamped_voltage - v_low) / (v_high - v_low)
                return int(round(soc_value))
        return 0

    def _battery_charge_state(self, current: float) -> str:
        if float(current) > 0.05:
            return "discharging"
        if float(current) < -0.05:
            return "charging"
        return "idle"

    def _is_battery_voltage_valid(self, voltage: float, rating: float) -> bool:
        if float(rating) >= 20.0:
            min_v, max_v = 21.0, 29.0
        else:
            min_v, max_v = 10.5, 14.8
        return float(min_v) <= float(voltage) <= float(max_v)

    def _ina219_fallback_voltage(self, raw_voltage: int) -> float:
        return round((int(raw_voltage) >> 3) * 0.004, 3)

    def _ina219_fallback_current(self, raw_current_source: int) -> float:
        return round(int(raw_current_source) * (3.2 / 32767), 4)

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

    def _resolve_variant_profile(self, sensor: dict[str, Any], module_config: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        sensor_variant = self._normalize_variant(sensor.get("variant"))
        strict_variant_mode = bool((module_config or {}).get("strict_variant_mode", False))

        if sensor_variant in VARIANT_PROFILES:
            profile = dict(VARIANT_PROFILES[sensor_variant])
            effective_variant = sensor_variant
        elif strict_variant_mode:
            return None, sensor_variant
        else:
            profile = dict(VARIANT_PROFILES["INA219"])
            effective_variant = "INA219"

        variant_profile_overrides = (module_config or {}).get("variant_profile_overrides", {})
        override_payload = variant_profile_overrides.get(sensor_variant) if isinstance(variant_profile_overrides, dict) else None
        if isinstance(override_payload, dict):
            profile.update({key: value for key, value in override_payload.items() if value is not None})

        sensor_variant_tuning = sensor.get("variant_tuning")
        if isinstance(sensor_variant_tuning, dict):
            profile.update({key: value for key, value in sensor_variant_tuning.items() if value is not None})

        return profile, effective_variant

    def _read_measurements(self, pi: Any, sensor: dict[str, Any], module_config: dict[str, Any]) -> dict[str, Any]:
        address = self._coerce_int(sensor.get("address"), -1)
        if address < 0:
            return {"connected": False, "status_detail": "invalid-address"}

        profile, effective_variant = self._resolve_variant_profile(sensor, module_config)
        if profile is None:
            return {"connected": False, "status_detail": "unsupported-variant:strict-mode"}

        calibration_value = self._coerce_int(sensor.get("calibration_value"), DEFAULT_CALIBRATION)

        handle = None
        try:
            handle = pi.i2c_open(1, address)

            if profile.get("requires_calibration"):
                # pigpio writes words as little-endian; swap so device gets big-endian calibration.
                calibration_register = int(profile.get("calibration_reg", CALIBRATION_REGISTER))
                pi.i2c_write_word_data(handle, calibration_register, self._swap_word_bytes(calibration_value))

            raw_voltage = self._read_register_16_raw(pi, handle, int(profile["voltage_reg"]))
            raw_current_source: int | None = None
            computed_current: float | None = None

            if profile.get("current_from_shunt"):
                shunt_register = int(profile.get("shunt_reg", 0x01))
                raw_current_source = self._read_register_16_signed(pi, handle, shunt_register)
            else:
                current_register = int(profile.get("current_reg", 0x04))
                raw_current_source = self._read_register_16_signed(pi, handle, current_register)
                if (
                    raw_current_source == 0
                    and profile.get("current_fallback_from_shunt")
                    and "shunt_reg" in profile
                ):
                    shunt_fallback = self._read_register_16_signed(pi, handle, int(profile.get("shunt_reg", 0x01)))
                    if shunt_fallback is not None and shunt_fallback != 0:
                        raw_current_source = shunt_fallback
                        shunt_shift = int(profile.get("shunt_shift", 0))
                        shunt_voltage = (raw_current_source >> shunt_shift) * self._coerce_float(profile.get("shunt_lsb"), 0.0)
                        shunt_resistance = self._coerce_float(profile.get("shunt_resistance_ohms"), 0.1)
                        if shunt_resistance > 0:
                            computed_current = shunt_voltage / shunt_resistance

            if raw_voltage is None or raw_current_source is None:
                return {"connected": False, "status_detail": "register-read-failed"}

            if raw_voltage == 0 and raw_current_source == 0:
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
            voltage_lsb = self._coerce_float(profile.get("voltage_lsb"), 0.0)
            current_lsb = self._coerce_float(profile.get("current_lsb"), 0.0)

            voltage = round((raw_voltage >> voltage_shift) * voltage_lsb, 3)

            if computed_current is None:
                if profile.get("current_from_shunt"):
                    shunt_shift = int(profile.get("shunt_shift", 0))
                    shunt_voltage = (raw_current_source >> shunt_shift) * self._coerce_float(profile.get("shunt_lsb"), 0.0)
                    shunt_resistance = self._coerce_float(profile.get("shunt_resistance_ohms"), 0.1)
                    if shunt_resistance <= 0:
                        return {"connected": False, "status_detail": "invalid-shunt-resistance"}
                    computed_current = shunt_voltage / shunt_resistance
                else:
                    computed_current = raw_current_source * current_lsb

            current = round(computed_current, 4)
            sensor_type = str(sensor.get("type") or "").strip().lower()
            if sensor_type in {"solar", "wind"}:
                current = abs(current)
            elif sensor_type in {"battery", "system"} and effective_variant in {"INA219", "INA226"} and abs(current) < 0.05:
                current = 0.0

            status_variant_note = effective_variant
            if sensor_type in {"battery", "system"}:
                rating = self._coerce_float(sensor.get("rating"), 12.0)
                if not self._is_battery_voltage_valid(voltage, rating):
                    # If the configured variant produces an impossible battery voltage,
                    # reinterpret the raw registers as INA219 to recover from common
                    # mis-configuration (e.g. INA219 sensor marked as INA226).
                    fallback_voltage = self._ina219_fallback_voltage(raw_voltage)
                    if self._is_battery_voltage_valid(fallback_voltage, rating):
                        voltage = fallback_voltage
                        if raw_current_source is not None:
                            fallback_current = self._ina219_fallback_current(raw_current_source)
                            if abs(fallback_current) < 0.05:
                                fallback_current = 0.0
                            current = fallback_current
                        effective_variant = "INA219"
                        status_variant_note = "INA219:auto-corrected"

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

            soc_value = None
            charging_state = ""
            if sensor_type in {"battery", "system"}:
                rating = self._coerce_float(sensor.get("rating"), 12.0)
                if sensor_type == "battery":
                    soc_value = self._estimate_battery_soc(voltage, rating)
                charging_state = self._battery_charge_state(current)

            return {
                "connected": True,
                "status": "connected",
                "status_detail": f"live-read:{status_variant_note}",
                "voltage": voltage,
                "current": current,
                "watts": power,
                "power": power,
                "state_of_charge": soc_value,
                "soc": soc_value,
                "charging_state": charging_state,
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
                measurement = self._read_measurements(pi, sensor, module_config)
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
                "state_of_charge": measurement.get("state_of_charge", measurement.get("soc")),
                "soc": measurement.get("soc", measurement.get("state_of_charge")),
                "charging_state": measurement.get("charging_state", ""),
                "calibration_value": self._coerce_int(sensor.get("calibration_value"), DEFAULT_CALIBRATION),
                "source_topic": f"poller://ina/{sensor.get('device_id') or 'device'}",
            }
            self.live_data_store.ingest_sensor(self.module_name, str(sensor.get("name") or sensor.get("address") or "sensor"), sensor_payload)

        self._close_stale_clients(active_device_keys)

        return self.build_snapshot(module_payload)

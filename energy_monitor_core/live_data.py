from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional


DISCONNECT_GRACE_SECONDS = 30


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    if number != number:
        return default
    return number


def normalize_sensor_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"solar", "wind", "battery", "charger", "system"}:
        return normalized
    if normalized in {"charge", "charging", "battery charger"}:
        return "charger"
    if normalized in {"flow", "net", "bidirectional", "bi-directional", "charge-discharge"}:
        return "system"
    return normalized or "unknown"


def normalize_i2c_address(value: Any) -> str:
    if value is None or value == "":
        return ""

    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return ""
        try:
            parsed = int(normalized, 16) if normalized.startswith("0x") or any(char in "abcdef" for char in normalized) else int(normalized, 10)
        except Exception:
            return value.strip()
        return f"0x{parsed:02x}"

    try:
        return f"0x{int(value):02x}"
    except Exception:
        return str(value)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class SensorDataStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._modules: Dict[str, Dict[str, Any]] = {}

    def _device_key(self, payload: Dict[str, Any], fallback: str) -> str:
        for key in ("device_id", "device", "mac", "source_topic"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return str(fallback or "").strip()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def ingest_sensor(self, module_name: str, sensor_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        module_key = str(module_name or "").strip()
        sensor_key = str(sensor_name or "").strip()
        if not module_key or not sensor_key:
            return {}

        data = payload if isinstance(payload, dict) else {}
        watts = _safe_float(data.get("watts", data.get("power", data.get("power_w", 0.0))))
        voltage = _safe_float(data.get("voltage", data.get("voltage_v", 0.0)))
        current = _safe_float(data.get("current", data.get("current_a", 0.0)))
        if current == 0.0 and voltage > 0:
            current = watts / voltage if voltage else 0.0

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        connected = bool(data.get("connected", True))
        status = str(data.get("status") or ("connected" if connected else "disconnected")).strip().lower()
        if status in {"online", "running", "active"}:
            connected = True
            status = "connected"
        elif status in {"offline", "disconnected", "inactive", "error", "lost"}:
            connected = False
            status = "disconnected"

        disconnect_mark = ""
        device_key = self._device_key(data, sensor_key)
        device_connected = bool(data.get("device_connected", connected))
        device_paired = bool(data.get("paired", False))
        connection_session = str(data.get("connection_session") or "").strip()

        record = {
            "name": str(data.get("name") or sensor_key).strip() or sensor_key,
            "type": normalize_sensor_type(data.get("type")),
            "watts": round(watts, 2),
            "voltage": round(voltage, 2),
            "current": round(current, 2),
            "power_trend": 0.0,
            "voltage_trend": 0.0,
            "current_trend": 0.0,
            "connected": connected,
            "status": status,
            "last_seen": data.get("last_seen") or now_iso,
            "source_topic": str(data.get("source_topic") or "").strip(),
            "raw": deepcopy(data),
        }

        with self._lock:
            module_bucket = self._modules.setdefault(module_key, {"sensors": {}, "updated_at": self._now()})
            previous = module_bucket["sensors"].get(sensor_key) if isinstance(module_bucket.get("sensors"), dict) else None
            devices = module_bucket.setdefault("devices", {}) if isinstance(module_bucket.get("devices"), dict) else module_bucket.setdefault("devices", {})
            previous_device = devices.get(device_key) if isinstance(devices, dict) and device_key else None

            if isinstance(previous, dict):
                previous_watts = _safe_float(previous.get("watts"))
                previous_voltage = _safe_float(previous.get("voltage"))
                previous_current = _safe_float(previous.get("current"))
                record["power_trend"] = round(record["watts"] - previous_watts, 3)
                record["voltage_trend"] = round(record["voltage"] - previous_voltage, 3)
                record["current_trend"] = round(record["current"] - previous_current, 3)

            if connected:
                record["last_seen"] = data.get("last_seen") or now_iso
            else:
                previous_connected = bool(previous.get("connected")) if isinstance(previous, dict) else False
                if previous_connected:
                    prior_mark = _parse_iso_datetime(previous.get("disconnect_mark")) if isinstance(previous, dict) else None
                    mark = prior_mark or now
                    elapsed = (now - mark).total_seconds()
                    if elapsed < DISCONNECT_GRACE_SECONDS:
                        record["connected"] = True
                        record["status"] = "connected"
                        record["last_seen"] = previous.get("last_seen") or record["last_seen"]
                        disconnect_mark = mark.isoformat()
                    else:
                        disconnect_mark = now_iso
                else:
                    disconnect_mark = now_iso

            if disconnect_mark:
                record["disconnect_mark"] = disconnect_mark

            if device_key:
                previous_device_connected = bool(previous_device.get("connected")) if isinstance(previous_device, dict) else False
                device_record = {
                    "connected": device_connected,
                    "paired": device_paired or bool(previous_device.get("paired")) if isinstance(previous_device, dict) else device_paired,
                    "last_seen": data.get("last_seen") or now_iso,
                    "connection_session": connection_session or (str(previous_device.get("connection_session") or "").strip() if isinstance(previous_device, dict) else ""),
                }
                if device_connected:
                    device_record["last_seen"] = data.get("last_seen") or now_iso
                elif previous_device_connected:
                    prior_mark = _parse_iso_datetime(previous_device.get("disconnect_mark")) if isinstance(previous_device, dict) else None
                    mark = prior_mark or now
                    elapsed = (now - mark).total_seconds()
                    if elapsed < DISCONNECT_GRACE_SECONDS:
                        device_record["connected"] = True
                        device_record["last_seen"] = previous_device.get("last_seen") or device_record["last_seen"]
                        device_record["disconnect_mark"] = mark.isoformat()
                    else:
                        device_record["disconnect_mark"] = now_iso
                else:
                    device_record["disconnect_mark"] = now_iso
                devices[device_key] = device_record

            module_bucket["sensors"][sensor_key] = record
            module_bucket["updated_at"] = now_iso
        return deepcopy(record)

    def ingest_module_snapshot(self, module_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        module_key = str(module_name or "").strip()
        if not module_key:
            return {}

        data = payload if isinstance(payload, dict) else {}
        sensor_payloads = data.get("sensors")
        if isinstance(sensor_payloads, list):
            for sensor_payload in sensor_payloads:
                if not isinstance(sensor_payload, dict):
                    continue
                self.ingest_sensor(module_key, str(sensor_payload.get("name") or sensor_payload.get("sensor") or "sensor"), sensor_payload)
        elif isinstance(sensor_payloads, dict):
            for sensor_name, sensor_payload in sensor_payloads.items():
                self.ingest_sensor(module_key, str(sensor_name), sensor_payload if isinstance(sensor_payload, dict) else {})

        module_connected = data.get("connected")
        if module_connected is not None:
            with self._lock:
                module_bucket = self._modules.setdefault(module_key, {"sensors": {}, "updated_at": self._now()})
                module_bucket["connected"] = bool(module_connected)
                module_bucket["updated_at"] = self._now()
        return self.get_module_bucket(module_key)

    def get_module_bucket(self, module_name: str) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._modules.get(str(module_name or "").strip(), {"sensors": {}, "updated_at": None}))

    def build_module_snapshot(
        self,
        module_name: str,
        sensor_config: Iterable[Dict[str, Any]],
        module_config: Optional[Dict[str, Any]] = None,
        active: bool = False,
        poll_interval: int = 10,
        definition: Optional[Dict[str, Any]] = None,
        backups: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        module_key = str(module_name or "").strip()
        module_bucket = self.get_module_bucket(module_key)
        live_sensors = module_bucket.get("sensors", {}) if isinstance(module_bucket, dict) else {}
        live_devices = module_bucket.get("devices", {}) if isinstance(module_bucket, dict) else {}
        sensor_rows: List[Dict[str, Any]] = []
        sensor_summary: Dict[str, Dict[str, Any]] = {}

        def _device_state_for(sensor_payload: Dict[str, Any]) -> Dict[str, Any]:
            device_key = str(sensor_payload.get("device_id") if sensor_payload.get("device_id") is not None else "").strip()
            if not device_key:
                return {}
            cached = live_devices.get(device_key, {}) if isinstance(live_devices, dict) else {}
            return cached if isinstance(cached, dict) else {}

        for index, sensor in enumerate(sensor_config if isinstance(sensor_config, Iterable) else []):
            if not isinstance(sensor, dict):
                continue
            sensor_name = str(sensor.get("name") or sensor.get("id") or f"sensor-{index + 1}").strip() or f"sensor-{index + 1}"
            sensor_type = normalize_sensor_type(sensor.get("type"))
            live = deepcopy(live_sensors.get(sensor_name, {})) if isinstance(live_sensors, dict) else {}
            device_state = _device_state_for(sensor)
            connected = bool(live.get("connected", False)) or bool(device_state.get("connected", False))
            watts = _safe_float(live.get("watts", 0.0))
            voltage = _safe_float(live.get("voltage", 0.0))
            current = _safe_float(live.get("current", 0.0))
            if connected and current == 0.0 and voltage > 0:
                current = watts / voltage if voltage else 0.0

            row = {
                "name": sensor_name,
                "type": sensor_type,
                "variant": str(sensor.get("variant") or sensor.get("source_device_type") or "").strip(),
                "device_id": sensor.get("device_id"),
                "address": normalize_i2c_address(sensor.get("address")) if module_name == "ina" else sensor.get("address"),
                "max_power": _safe_float(sensor.get("max_power")),
                "rating": _safe_float(sensor.get("rating")),
                "watts": round(watts, 2),
                "voltage": round(voltage, 2),
                "current": round(current, 2),
                "soc": round(_safe_float(live.get("soc", live.get("state_of_charge", live.get("raw", {}).get("state_of_charge", 0.0)))), 2),
                "power_trend": round(_safe_float(live.get("power_trend", live.get("raw", {}).get("power_trend", 0.0))), 3),
                "voltage_trend": round(_safe_float(live.get("voltage_trend", live.get("raw", {}).get("voltage_trend", 0.0))), 3),
                "current_trend": round(_safe_float(live.get("current_trend", live.get("raw", {}).get("current_trend", 0.0))), 3),
                "connected": connected,
                "status": str(live.get("status") or ("connected" if connected else "disconnected")).strip().lower(),
                "status_detail": str(live.get("status_detail") or live.get("raw", {}).get("status_detail") or "").strip(),
                "last_seen": live.get("last_seen"),
                "source_topic": live.get("source_topic"),
                "charging_state": str(live.get("charging_state") or live.get("raw", {}).get("charging_state") or "").strip(),
                "charge_mode": str(live.get("charge_mode") or live.get("raw", {}).get("charge_mode") or "").strip(),
                "rssi": live.get("rssi", live.get("raw", {}).get("rssi")),
                "device_connected": connected,
                "paired": bool(device_state.get("paired", False)) or bool(live.get("paired", False)),
                "config": deepcopy(sensor),
            }
            sensor_rows.append(row)

            bucket = sensor_summary.setdefault(sensor_type, {
                "type": sensor_type,
                "sensor_count": 0,
                "connected_count": 0,
                "watts": 0.0,
                "voltage": 0.0,
                "current": 0.0,
            })
            bucket["sensor_count"] += 1
            if connected:
                bucket["connected_count"] += 1
                bucket["watts"] += watts
                bucket["voltage"] += voltage
                bucket["current"] += current

        for bucket in sensor_summary.values():
            bucket["watts"] = round(bucket["watts"], 2)
            bucket["voltage"] = round(bucket["voltage"], 2)
            bucket["current"] = round(bucket["current"], 2)

        connected_sensor_count = sum(1 for row in sensor_rows if row.get("connected"))
        total_watts = round(sum(row["watts"] for row in sensor_rows if row.get("connected")), 2)
        total_voltage = round(sum(row["voltage"] for row in sensor_rows if row.get("connected")), 2)
        total_current = round(sum(row["current"] for row in sensor_rows if row.get("connected")), 2)

        devices = module_config.get("devices", []) if isinstance(module_config, dict) else []
        device_status_summary: dict[str, dict[str, Any]] = {}
        for device in devices if isinstance(devices, list) else []:
            if not isinstance(device, dict):
                continue
            device_key = str(device.get("id") if device.get("id") is not None else device.get("name") or "").strip()
            if not device_key:
                continue
            cached_device = live_devices.get(device_key, {}) if isinstance(live_devices, dict) else {}
            device_status_summary[device_key] = {
                "id": device.get("id"),
                "name": str(device.get("name") or "").strip() or device_key,
                "connected": bool(cached_device.get("connected", False)),
                "paired": bool(cached_device.get("paired", False)) or bool(device.get("paired")),
                "connection_session": str(cached_device.get("connection_session") or "").strip(),
            }

        for row in sensor_rows:
            device_key = str(row.get("device_id") if row.get("device_id") is not None else "").strip()
            if not device_key:
                continue
            live = live_sensors.get(str(row.get("name") or ""), {}) if isinstance(live_sensors, dict) else {}
            raw = live.get("raw", {}) if isinstance(live, dict) else {}
            cached_device = live_devices.get(device_key, {}) if isinstance(live_devices, dict) else {}
            device_connected = bool(raw.get("device_connected", row.get("connected", False))) or bool(cached_device.get("connected", False)) or bool(row.get("connected", False))
            if device_key not in device_status_summary:
                device_status_summary[device_key] = {
                    "id": row.get("device_id"),
                    "name": device_key,
                    "connected": False,
                    "paired": False,
                    "connection_session": "",
                }
            if device_connected:
                device_status_summary[device_key]["connected"] = True
            if bool(raw.get("paired", row.get("paired", False))) or bool(cached_device.get("paired", False)):
                device_status_summary[device_key]["paired"] = True
            raw_session = str(raw.get("connection_session") or "").strip()
            if raw_session:
                device_status_summary[device_key]["connection_session"] = raw_session

        device_count = len(device_status_summary)
        connected_device_count = sum(1 for item in device_status_summary.values() if item.get("connected"))
        paired_device_count = sum(1 for item in device_status_summary.values() if item.get("paired"))
        if device_count > 0:
            if connected_device_count == device_count:
                module_status = "connected"
            elif connected_device_count > 0:
                module_status = "partial"
            elif paired_device_count > 0 and module_key == "victron":
                module_status = "paired"
            else:
                module_status = "disconnected"
        else:
            module_status = "connected" if connected_sensor_count else "disconnected"

        return {
            "module": module_key,
            "updated_at": module_bucket.get("updated_at") or self._now(),
            "active": bool(active),
            "status": module_status,
            "poll_interval": poll_interval,
            "device_count": device_count,
            "connected_device_count": connected_device_count,
            "paired_device_count": paired_device_count,
            "sensor_count": len(sensor_rows),
            "connected_sensor_count": connected_sensor_count,
            "watts": total_watts,
            "voltage": total_voltage,
            "current": total_current,
            "device_status_summary": device_status_summary,
            "sensor_rows": sensor_rows,
            "sensor_type_summary": sensor_summary,
            "module_config": deepcopy(module_config) if isinstance(module_config, dict) else {},
            "sensor_config": deepcopy(list(sensor_config)) if sensor_config else [],
            "definition": deepcopy(definition) if isinstance(definition, dict) else {},
            "backups": deepcopy(backups) if isinstance(backups, list) else [],
        }

    def get_full_live_data(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._modules)


SENSOR_DATA_STORE = SensorDataStore()
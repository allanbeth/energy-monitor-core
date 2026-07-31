from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional


DISCONNECT_GRACE_SECONDS = 6


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
    if normalized in {"solar", "wind", "battery", "charger"}:
        return normalized
    if normalized in {"charge", "charging", "battery charger"}:
        return "charger"
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

        record = {
            "name": str(data.get("name") or sensor_key).strip() or sensor_key,
            "type": normalize_sensor_type(data.get("type")),
            "watts": round(watts, 2),
            "voltage": round(voltage, 2),
            "current": round(current, 2),
            "connected": connected,
            "status": status,
            "last_seen": data.get("last_seen") or now_iso,
            "source_topic": str(data.get("source_topic") or "").strip(),
            "raw": deepcopy(data),
        }

        with self._lock:
            module_bucket = self._modules.setdefault(module_key, {"sensors": {}, "updated_at": self._now()})
            previous = module_bucket["sensors"].get(sensor_key) if isinstance(module_bucket.get("sensors"), dict) else None

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
        sensor_rows: List[Dict[str, Any]] = []
        sensor_summary: Dict[str, Dict[str, Any]] = {}

        for index, sensor in enumerate(sensor_config if isinstance(sensor_config, Iterable) else []):
            if not isinstance(sensor, dict):
                continue
            sensor_name = str(sensor.get("name") or sensor.get("id") or f"sensor-{index + 1}").strip() or f"sensor-{index + 1}"
            sensor_type = normalize_sensor_type(sensor.get("type"))
            live = deepcopy(live_sensors.get(sensor_name, {})) if isinstance(live_sensors, dict) else {}
            connected = bool(live.get("connected", False))
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
                "connected": connected,
                "status": str(live.get("status") or ("connected" if connected else "disconnected")).strip().lower(),
                "last_seen": live.get("last_seen"),
                "source_topic": live.get("source_topic"),
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
            device_status_summary[device_key] = {
                "id": device.get("id"),
                "name": str(device.get("name") or "").strip() or device_key,
                "connected": False,
            }

        for row in sensor_rows:
            device_key = str(row.get("device_id") if row.get("device_id") is not None else "").strip()
            if not device_key:
                continue
            live = live_sensors.get(str(row.get("name") or ""), {}) if isinstance(live_sensors, dict) else {}
            raw = live.get("raw", {}) if isinstance(live, dict) else {}
            device_connected = bool(raw.get("device_connected", row.get("connected", False)))
            if device_key not in device_status_summary:
                device_status_summary[device_key] = {
                    "id": row.get("device_id"),
                    "name": device_key,
                    "connected": False,
                }
            if device_connected:
                device_status_summary[device_key]["connected"] = True

        device_count = len(device_status_summary)
        connected_device_count = sum(1 for item in device_status_summary.values() if item.get("connected"))
        if device_count > 0:
            module_status = "connected" if connected_device_count == device_count else ("partial" if connected_device_count > 0 else "disconnected")
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
            "live_sensors": live_sensors,
        }

    def get_full_live_data(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._modules)


SENSOR_DATA_STORE = SensorDataStore()
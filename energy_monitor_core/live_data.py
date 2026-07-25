from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional


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
    if normalized in {"solar", "wind", "battery"}:
        return normalized
    if normalized in {"charger", "charge", "charging"}:
        return "battery"
    return normalized or "unknown"


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
        watts = _safe_float(data.get("watts", data.get("power")))
        voltage = _safe_float(data.get("voltage", data.get("rating")))
        current = _safe_float(data.get("current"))
        if current == 0.0 and voltage > 0:
            current = watts / voltage if voltage else 0.0

        connected = bool(data.get("connected", True))
        status = str(data.get("status") or ("connected" if connected else "disconnected")).strip().lower()
        if status in {"online", "running", "active"}:
            connected = True
            status = "connected"
        elif status in {"offline", "disconnected", "inactive", "error", "lost"}:
            connected = False
            status = "disconnected"

        record = {
            "name": str(data.get("name") or sensor_key).strip() or sensor_key,
            "type": normalize_sensor_type(data.get("type")),
            "watts": round(watts, 2),
            "voltage": round(voltage, 2),
            "current": round(current, 2),
            "connected": connected,
            "status": status,
            "last_seen": data.get("last_seen") or self._now(),
            "source_topic": str(data.get("source_topic") or "").strip(),
            "raw": deepcopy(data),
        }

        with self._lock:
            module_bucket = self._modules.setdefault(module_key, {"sensors": {}, "updated_at": self._now()})
            module_bucket["sensors"][sensor_key] = record
            module_bucket["updated_at"] = self._now()
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
            watts = _safe_float(live.get("watts", sensor.get("max_power"))) if connected else _safe_float(live.get("watts", 0.0))
            voltage = _safe_float(live.get("voltage", sensor.get("rating"))) if connected else _safe_float(live.get("voltage", 0.0))
            current = _safe_float(live.get("current")) if connected else _safe_float(live.get("current", 0.0))
            if connected and current == 0.0 and voltage > 0:
                current = watts / voltage if voltage else 0.0

            row = {
                "name": sensor_name,
                "type": sensor_type,
                "variant": str(sensor.get("variant") or sensor.get("source_device_type") or "").strip(),
                "device_id": sensor.get("device_id"),
                "address": sensor.get("address"),
                "max_power": _safe_float(sensor.get("max_power")),
                "rating": _safe_float(sensor.get("rating")),
                "watts": round(watts, 2),
                "voltage": round(voltage, 2),
                "current": round(current, 2),
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

        return {
            "module": module_key,
            "updated_at": module_bucket.get("updated_at") or self._now(),
            "active": bool(active),
            "status": "connected" if connected_sensor_count else "disconnected",
            "poll_interval": poll_interval,
            "device_count": len(module_config.get("devices", [])) if isinstance(module_config, dict) else 0,
            "sensor_count": len(sensor_rows),
            "connected_sensor_count": connected_sensor_count,
            "watts": total_watts,
            "voltage": total_voltage,
            "current": total_current,
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
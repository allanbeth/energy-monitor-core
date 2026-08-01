# isort: skip_file

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import json
import math
import threading
import time

from .live_data import SENSOR_DATA_STORE
from .logging import get_logger, get_module_logger
from .pollers import get_module_poller


logger = get_logger(__name__)


def _to_pretty_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=False, default=str)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number):
        return default
    return number


def _sensor_name(sensor: dict[str, Any], fallback_index: int) -> str:
    name = str(sensor.get("name") or sensor.get("id") or f"sensor-{fallback_index + 1}").strip()
    return name or f"sensor-{fallback_index + 1}"


def _normalize_sensor_type(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    if normalized in {"solar", "wind", "battery", "charger"}:
        return normalized
    if normalized in {"charge", "charging", "battery charger"}:
        return "charger"
    return normalized or "unknown"


class ModuleRuntime:
    def __init__(self, app_root: Path, config_manager: Any, module_name: str, live_data_store: Any = None):
        self.app_root = Path(app_root)
        self.config_manager = config_manager
        self.module_name = module_name
        self.live_data_store = live_data_store or SENSOR_DATA_STORE
        self.logger = get_module_logger(self.app_root, module_name)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.latest_snapshot: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []
        self.poller = get_module_poller(self.module_name, self.config_manager, self.live_data_store)
        self.poll_intervals = self._load_poll_intervals()
        self.poll_interval = min(self.poll_intervals.values()) if self.poll_intervals else self._determine_poll_interval()
        self._next_poll_by_type: dict[str, float] = {}

    def _load_poll_intervals(self) -> dict[str, int]:
        payload = self.config_manager.get_module_payload(self.module_name)
        module_config = payload.get("module_config", {}) if isinstance(payload, dict) else {}
        intervals = module_config.get("poll_intervals", {}) if isinstance(module_config, dict) else {}
        normalized: dict[str, int] = {}
        if isinstance(intervals, dict):
            for sensor_type, value in intervals.items():
                interval_value = _safe_float(value, 0.0)
                if interval_value <= 0:
                    continue
                normalized[_normalize_sensor_type(sensor_type)] = max(5, int(interval_value))
        if not normalized and self.poller is not None:
            fallback = self.poller.get_poll_interval()
            if fallback:
                normalized = {"solar": max(5, int(fallback)), "wind": max(5, int(fallback)), "battery": max(5, int(fallback))}
        if not normalized:
            normalized = {"solar": 10, "wind": 10, "battery": 10}
        return normalized

    def _determine_poll_interval(self) -> int:
        if self.poller is not None:
            interval = self.poller.get_poll_interval()
            if interval:
                return max(5, int(interval))

        module_payload = self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {})
        intervals = module_config.get("poll_intervals", {}) if isinstance(module_config, dict) else {}
        if isinstance(intervals, dict):
            values = [int(value) for value in intervals.values() if str(value).isdigit() or isinstance(value, (int, float))]
            if values:
                return max(5, min(values))
        return 10

    def _refresh_poll_configuration(self, now: float) -> None:
        new_intervals = self._load_poll_intervals()
        if new_intervals != self.poll_intervals:
            self.poll_intervals = new_intervals
            self.poll_interval = min(self.poll_intervals.values()) if self.poll_intervals else 10
            # Re-arm schedule immediately so interval updates are applied without restart.
            self._next_poll_by_type = {sensor_type: now for sensor_type in self.poll_intervals}

        for sensor_type in self.poll_intervals:
            self._next_poll_by_type.setdefault(sensor_type, now)

    def _collect_due_sensor_types(self, now: float) -> set[str]:
        due: set[str] = set()
        for sensor_type, next_due in self._next_poll_by_type.items():
            if now >= next_due:
                due.add(sensor_type)
        return due

    def _mark_polled(self, due_sensor_types: set[str], now: float) -> None:
        for sensor_type in due_sensor_types:
            interval = max(1, int(self.poll_intervals.get(sensor_type, self.poll_interval)))
            self._next_poll_by_type[sensor_type] = now + interval

    def _seconds_until_next_poll(self, now: float) -> float:
        if not self._next_poll_by_type:
            return 1.0
        next_due = min(self._next_poll_by_type.values())
        return max(0.2, min(next_due - now, float(self.poll_interval)))

    def _build_sensor_rows(self, sensor_config: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, sensor in enumerate(sensor_config if isinstance(sensor_config, list) else []):
            if not isinstance(sensor, dict):
                continue

            watts = _safe_float(sensor.get("watts", sensor.get("max_power")))
            voltage = _safe_float(sensor.get("voltage", sensor.get("rating")))
            current = watts / voltage if voltage > 0 else 0.0
            usage = current if current > 0 else watts

            rows.append({
                "name": _sensor_name(sensor, index),
                "type": _normalize_sensor_type(sensor.get("type")),
                "variant": str(sensor.get("variant") or sensor.get("source_device_type") or "").strip(),
                "device_id": sensor.get("device_id"),
                "address": sensor.get("address"),
                "power": round(watts, 2),
                "watts": round(watts, 2),
                "usage": round(usage, 2),
                "rating": round(voltage, 2),
                "voltage": round(voltage, 2),
                "current": round(current, 2),
            })
        return rows

    def _build_sensor_type_summary(self, sensor_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for row in sensor_rows:
            sensor_type = _normalize_sensor_type(row.get("type"))
            bucket = summary.setdefault(sensor_type, {
                "type": sensor_type,
                "count": 0,
                "power": 0.0,
                "watts": 0.0,
                "usage": 0.0,
                "rating": 0.0,
                "voltage": 0.0,
                "current": 0.0,
            })
            bucket["count"] += 1
            bucket["power"] += _safe_float(row.get("power"))
            bucket["watts"] += _safe_float(row.get("watts", row.get("power")))
            bucket["usage"] += _safe_float(row.get("usage"))
            bucket["rating"] += _safe_float(row.get("rating"))
            bucket["voltage"] += _safe_float(row.get("voltage"))
            bucket["current"] += _safe_float(row.get("current"))

        for bucket in summary.values():
            bucket["power"] = round(bucket["power"], 2)
            bucket["watts"] = round(bucket["watts"], 2)
            bucket["usage"] = round(bucket["usage"], 2)
            bucket["rating"] = round(bucket["rating"], 2)
            bucket["voltage"] = round(bucket["voltage"], 2)
            bucket["current"] = round(bucket["current"], 2)
        return summary

    def _build_snapshot(self, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        payload = self.config_manager.get_module_payload(self.module_name)
        if self.poller is not None:
            snapshot = self.poller.poll(payload, due_sensor_types=due_sensor_types)
            if isinstance(snapshot, dict) and snapshot:
                return snapshot

        module_config = payload.get("module_config", {}) if isinstance(payload, dict) else {}
        sensor_config = payload.get("sensor_config", []) if isinstance(payload, dict) else []
        return self.live_data_store.build_module_snapshot(
            self.module_name,
            sensor_config,
            module_config=module_config,
            active=bool(payload.get("active", False)),
            poll_interval=self.poll_interval,
            definition=payload.get("definitions", {}),
            backups=payload.get("backups", []),
        )

    def build_cached_snapshot(self) -> dict[str, Any]:
        payload = self.config_manager.get_module_payload(self.module_name)
        module_config = payload.get("module_config", {}) if isinstance(payload, dict) else {}
        sensor_config = payload.get("sensor_config", []) if isinstance(payload, dict) else []
        return self.live_data_store.build_module_snapshot(
            self.module_name,
            sensor_config,
            module_config=module_config,
            active=bool(payload.get("active", False)),
            poll_interval=self.poll_interval,
            definition=payload.get("definitions", {}),
            backups=payload.get("backups", []),
        )

    def poll_once(self, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        snapshot = self._build_snapshot(due_sensor_types)
        self.latest_snapshot = snapshot
        self.history.append(snapshot)
        self.history = self.history[-50:]
        self.logger.info("Polled module snapshot: %s", _to_pretty_json({
            "module": snapshot["module"],
            "updated_at": snapshot["updated_at"],
            "status": snapshot["status"],
            "device_count": snapshot["device_count"],
            "sensor_count": snapshot["sensor_count"],
            "sensor_types": list(snapshot.get("sensor_type_summary", {}).keys()),
        }))
        return snapshot

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return

        self.stop_event.clear()

        def _loop() -> None:
            while not self.stop_event.is_set():
                try:
                    now = time.monotonic()
                    self._refresh_poll_configuration(now)
                    due_sensor_types = self._collect_due_sensor_types(now)
                    if due_sensor_types:
                        self.poll_once(due_sensor_types)
                        self._mark_polled(due_sensor_types, now)
                except Exception:
                    self.logger.exception("Module poll failed")
                wait_seconds = self._seconds_until_next_poll(time.monotonic())
                self.stop_event.wait(wait_seconds)

        self.thread = threading.Thread(target=_loop, name=f"module-{self.module_name}", daemon=True)
        self.thread.start()
        self.logger.info("Module runtime started")

    def stop(self) -> None:
        self.stop_event.set()
        if self.poller is not None and hasattr(self.poller, "shutdown"):
            try:
                self.poller.shutdown()
            except Exception:
                self.logger.exception("Module poller shutdown failed")
        self.logger.info("Module runtime stopped")


class ModuleRuntimeManager:
    def __init__(self, app_root: Path, config_manager: Any, live_data_store: Any = None):
        self.app_root = Path(app_root)
        self.config_manager = config_manager
        self.live_data_store = live_data_store or SENSOR_DATA_STORE
        self.runtimes: dict[str, ModuleRuntime] = {}
        self._lock = threading.RLock()

    def sync_from_config(self) -> None:
        with self._lock:
            active_modules = set(self.config_manager.get_active_modules().keys())

            for module_name in list(self.runtimes.keys()):
                if module_name not in active_modules:
                    self.runtimes[module_name].stop()
                    self.runtimes.pop(module_name, None)

            for module_name in active_modules:
                runtime = self.runtimes.get(module_name)
                if runtime is None:
                    runtime = ModuleRuntime(self.app_root, self.config_manager, module_name, self.live_data_store)
                    self.runtimes[module_name] = runtime
                    runtime.start()

    def get_full_live_data(self) -> dict[str, Any]:
        with self._lock:
            return {module_name: runtime.latest_snapshot for module_name, runtime in self.runtimes.items()}

    def get_module_snapshot(self, module_name: str) -> dict[str, Any]:
        runtime = self.get_runtime(module_name)
        if runtime is None:
            return {}
        if not runtime.latest_snapshot:
            return runtime.poll_once()
        return runtime.latest_snapshot

    def get_module_snapshot_cached(self, module_name: str) -> dict[str, Any]:
        runtime = self.get_runtime(module_name)
        if runtime is None:
            return {}
        if runtime.latest_snapshot:
            return runtime.latest_snapshot
        return runtime.build_cached_snapshot()

    def get_module_sensor_rows(self, module_name: str) -> list[dict[str, Any]]:
        snapshot = self.get_module_snapshot(module_name)
        rows = snapshot.get("sensor_rows", []) if isinstance(snapshot, dict) else []
        return rows if isinstance(rows, list) else []

    def get_module_sensor_type_summary(self, module_name: str) -> dict[str, dict[str, Any]]:
        snapshot = self.get_module_snapshot(module_name)
        summary = snapshot.get("sensor_type_summary", {}) if isinstance(snapshot, dict) else {}
        return summary if isinstance(summary, dict) else {}

    def get_dashboard_sensor_type_summary(self) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for module_name in self.runtimes:
            module_summary = self.get_module_sensor_type_summary(module_name)
            for sensor_type, bucket in module_summary.items():
                target = summary.setdefault(sensor_type, {
                    "type": sensor_type,
                    "watts": 0.0,
                    "voltage": 0.0,
                    "current": 0.0,
                    "sensor_count": 0,
                    "connected_count": 0,
                })
                target["watts"] += _safe_float(bucket.get("watts", bucket.get("power")))
                target["voltage"] += _safe_float(bucket.get("voltage"))
                target["current"] += _safe_float(bucket.get("current"))
                target["sensor_count"] += int(bucket.get("sensor_count", 0) or 0)
                target["connected_count"] += int(bucket.get("connected_count", 0) or 0)

        for bucket in summary.values():
            bucket["watts"] = round(bucket["watts"], 2)
            bucket["voltage"] = round(bucket["voltage"], 2)
            bucket["current"] = round(bucket["current"], 2)
        return summary

    def get_aggregate_trends(self, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
        limit = max(2, int(limit or 8))
        buckets: dict[str, list[dict[str, Any]]] = {
            "overall": [],
            "solar": [],
            "wind": [],
            "battery": [],
            "charger": [],
            "battery_charge": [],
            "battery_discharge": [],
            "estimated_load": [],
        }

        histories: list[list[dict[str, Any]]] = []
        for runtime in self.runtimes.values():
            history = runtime.history[-limit:] if runtime.history else []
            histories.append(history)

        max_length = max((len(history) for history in histories), default=0)
        for offset in reversed(range(max_length)):
            aggregate = {
                "overall": {"watts": 0.0, "voltage": 0.0, "current": 0.0, "sensor_count": 0},
                "solar": {"watts": 0.0, "voltage": 0.0, "current": 0.0, "sensor_count": 0},
                "wind": {"watts": 0.0, "voltage": 0.0, "current": 0.0, "sensor_count": 0},
                "battery": {"watts": 0.0, "voltage": 0.0, "current": 0.0, "sensor_count": 0},
                "charger": {"watts": 0.0, "voltage": 0.0, "current": 0.0, "sensor_count": 0},
            }

            for history in histories:
                snapshot = history[-(offset + 1)] if len(history) > offset else None
                if not isinstance(snapshot, dict):
                    continue
                for sensor_type in ("solar", "wind", "battery", "charger"):
                    bucket = snapshot.get("sensor_type_summary", {}).get(sensor_type, {}) if isinstance(snapshot.get("sensor_type_summary", {}), dict) else {}
                    aggregate[sensor_type]["watts"] += _safe_float(bucket.get("watts", bucket.get("power")))
                    aggregate[sensor_type]["voltage"] += _safe_float(bucket.get("voltage"))
                    aggregate[sensor_type]["current"] += _safe_float(bucket.get("current"))
                    aggregate[sensor_type]["sensor_count"] += int(bucket.get("sensor_count", 0) or 0)

                    aggregate["overall"]["watts"] += _safe_float(bucket.get("watts", bucket.get("power")))
                    aggregate["overall"]["voltage"] += _safe_float(bucket.get("voltage"))
                    aggregate["overall"]["current"] += _safe_float(bucket.get("current"))
                    aggregate["overall"]["sensor_count"] += int(bucket.get("sensor_count", 0) or 0)

            for sensor_type, bucket in aggregate.items():
                buckets[sensor_type].append({
                    "watts": round(bucket["watts"], 2),
                    "voltage": round(bucket["voltage"], 2),
                    "current": round(bucket["current"], 2),
                    "sensor_count": bucket["sensor_count"],
                })

            battery_net_watts = _safe_float(aggregate["battery"]["watts"])
            battery_discharge_watts = max(0.0, battery_net_watts)
            battery_charge_watts = max(0.0, -battery_net_watts)
            source_watts = (
                max(0.0, _safe_float(aggregate["solar"]["watts"]))
                + max(0.0, _safe_float(aggregate["wind"]["watts"]))
                + max(0.0, _safe_float(aggregate["charger"]["watts"]))
            )
            estimated_load_watts = max(0.0, source_watts + battery_discharge_watts - battery_charge_watts)

            buckets["battery_discharge"].append({
                "watts": round(battery_discharge_watts, 2),
                "voltage": round(aggregate["battery"]["voltage"], 2),
                "current": round(max(0.0, _safe_float(aggregate["battery"]["current"])), 2),
                "sensor_count": int(aggregate["battery"]["sensor_count"]),
            })
            buckets["battery_charge"].append({
                "watts": round(battery_charge_watts, 2),
                "voltage": round(aggregate["battery"]["voltage"], 2),
                "current": round(max(0.0, -_safe_float(aggregate["battery"]["current"])), 2),
                "sensor_count": int(aggregate["battery"]["sensor_count"]),
            })
            buckets["estimated_load"].append({
                "watts": round(estimated_load_watts, 2),
                "voltage": round(aggregate["overall"]["voltage"], 2),
                "current": round(aggregate["overall"]["current"], 2),
                "sensor_count": int(aggregate["overall"]["sensor_count"]),
            })

        return buckets

    def get_aggregate_totals(self) -> dict[str, Any]:
        live_data = self.get_full_live_data()
        aggregate = {
            "modules": len(live_data),
            "sensor_count": 0,
            "connected_sensor_count": 0,
            "device_count": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "overall": {"sensor_count": 0, "connected_count": 0, "watts": 0.0, "voltage": 0.0, "current": 0.0},
            "solar": {"sensor_count": 0, "connected_count": 0, "watts": 0.0, "voltage": 0.0, "current": 0.0},
            "wind": {"sensor_count": 0, "connected_count": 0, "watts": 0.0, "voltage": 0.0, "current": 0.0},
            "battery": {"sensor_count": 0, "connected_count": 0, "watts": 0.0, "voltage": 0.0, "current": 0.0},
            "charger": {"sensor_count": 0, "connected_count": 0, "watts": 0.0, "voltage": 0.0, "current": 0.0},
            "derived": {
                "battery_charge_watts": 0.0,
                "battery_discharge_watts": 0.0,
                "source_watts": 0.0,
                "estimated_load_watts": 0.0,
                "battery_sensor_count": 0,
            },
        }
        for snapshot in live_data.values():
            aggregate["sensor_count"] += int(snapshot.get("sensor_count", 0) or 0)
            aggregate["connected_sensor_count"] += int(snapshot.get("connected_sensor_count", 0) or 0)
            aggregate["device_count"] += int(snapshot.get("device_count", 0) or 0)
            for sensor_type in ("solar", "wind", "battery", "charger"):
                bucket = snapshot.get("sensor_type_summary", {}).get(sensor_type, {})
                target = aggregate[sensor_type]
                target["sensor_count"] += int(bucket.get("sensor_count", 0) or 0)
                target["connected_count"] += int(bucket.get("connected_count", 0) or 0)
                target["watts"] += _safe_float(bucket.get("watts"))
                target["voltage"] += _safe_float(bucket.get("voltage"))
                target["current"] += _safe_float(bucket.get("current"))

                aggregate["overall"]["sensor_count"] += int(bucket.get("sensor_count", 0) or 0)
                aggregate["overall"]["connected_count"] += int(bucket.get("connected_count", 0) or 0)
                aggregate["overall"]["watts"] += _safe_float(bucket.get("watts"))
                aggregate["overall"]["voltage"] += _safe_float(bucket.get("voltage"))
                aggregate["overall"]["current"] += _safe_float(bucket.get("current"))

            sensor_rows = snapshot.get("sensor_rows", []) if isinstance(snapshot, dict) else []
            for row in sensor_rows if isinstance(sensor_rows, list) else []:
                if not isinstance(row, dict):
                    continue
                if not bool(row.get("connected", False)):
                    continue
                if _normalize_sensor_type(row.get("type")) != "battery":
                    continue

                watts = _safe_float(row.get("watts"))
                aggregate["derived"]["battery_sensor_count"] += 1
                if watts >= 0:
                    aggregate["derived"]["battery_discharge_watts"] += watts
                else:
                    aggregate["derived"]["battery_charge_watts"] += abs(watts)

        for sensor_type in ("overall", "solar", "wind", "battery", "charger"):
            for key in ("watts", "voltage", "current"):
                aggregate[sensor_type][key] = round(aggregate[sensor_type][key], 2)

        aggregate["derived"]["battery_charge_watts"] = round(_safe_float(aggregate["derived"].get("battery_charge_watts")), 2)
        aggregate["derived"]["battery_discharge_watts"] = round(_safe_float(aggregate["derived"].get("battery_discharge_watts")), 2)
        aggregate["derived"]["source_watts"] = round(
            max(0.0, _safe_float(aggregate["solar"].get("watts")))
            + max(0.0, _safe_float(aggregate["wind"].get("watts")))
            + max(0.0, _safe_float(aggregate["charger"].get("watts"))),
            2,
        )
        aggregate["derived"]["estimated_load_watts"] = round(
            max(
                0.0,
                _safe_float(aggregate["derived"].get("source_watts"))
                + _safe_float(aggregate["derived"].get("battery_discharge_watts"))
                - _safe_float(aggregate["derived"].get("battery_charge_watts")),
            ),
            2,
        )
        return aggregate

    def get_runtime(self, module_name: str) -> ModuleRuntime | None:
        return self.runtimes.get(module_name)

    def get_module_history(self, module_name: str) -> list[dict[str, Any]]:
        runtime = self.get_runtime(module_name)
        if runtime is None:
            return []
        return deepcopy(runtime.history)

    def refresh_module(self, module_name: str) -> None:
        with self._lock:
            runtime = self.runtimes.get(module_name)
            if runtime:
                runtime.poll_once()

    def shutdown(self) -> None:
        with self._lock:
            for runtime in self.runtimes.values():
                runtime.stop()
            self.runtimes.clear()

from __future__ import annotations

from threading import RLock
from typing import Any

from .base import BaseModulePoller

try:
    from pymodbus.client import ModbusSerialClient, ModbusTcpClient
except Exception:  # pragma: no cover - optional runtime dependency
    ModbusSerialClient = None
    ModbusTcpClient = None


class ModulePoller(BaseModulePoller):
    def __init__(self, module_name: str, config_manager: Any, live_data_store: Any = None):
        super().__init__(module_name, config_manager, live_data_store)
        self._clients: dict[str, Any] = {}
        self._client_lock = RLock()

    def _build_client(self, device: dict[str, Any]):
        connection_type = str(device.get("connection_type") or "serial_usb").strip().lower()
        timeout = float(device.get("timeout") or device.get("modbus", {}).get("timeout", 1))
        if connection_type in {"serial", "serial_usb", "rtu"} and ModbusSerialClient is not None:
            serial = device.get("serial", {}) if isinstance(device.get("serial", {}), dict) else {}
            return ModbusSerialClient(
                port=str(serial.get("port") or "/dev/ttyUSB0"),
                baudrate=int(serial.get("baudrate") or 9600),
                bytesize=int(serial.get("bytesize") or 8),
                parity=str(serial.get("parity") or "N"),
                stopbits=int(serial.get("stopbits") or 1),
                timeout=timeout,
                method=str(serial.get("method") or "rtu"),
            )
        if ModbusTcpClient is not None:
            tcp = device.get("tcp", {}) if isinstance(device.get("tcp", {}), dict) else {}
            return ModbusTcpClient(
                host=str(tcp.get("host") or device.get("host") or "127.0.0.1"),
                port=int(tcp.get("port") or device.get("port") or 502),
                timeout=timeout,
            )
        return None

    def _disconnect_client(self, client: Any) -> None:
        if client is None:
            return
        if hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass

    def _get_device_key(self, device: dict[str, Any]) -> str:
        key = str(device.get("id") or "").strip()
        if key:
            return key
        tcp = device.get("tcp", {}) if isinstance(device.get("tcp"), dict) else {}
        serial = device.get("serial", {}) if isinstance(device.get("serial"), dict) else {}
        host = str(tcp.get("host") or device.get("host") or "").strip()
        port = str(tcp.get("port") or device.get("port") or "").strip()
        serial_port = str(serial.get("port") or "").strip()
        if host:
            return f"tcp:{host}:{port or '502'}"
        if serial_port:
            return f"serial:{serial_port}"
        return "mppt-device"

    def _ensure_client(self, device_key: str, device: dict[str, Any]) -> Any:
        with self._client_lock:
            client = self._clients.get(device_key)
            if client is None:
                client = self._build_client(device)
                self._clients[device_key] = client
            return client

    def _ensure_connected(self, client: Any) -> bool:
        if client is None:
            return False
        if bool(getattr(client, "connected", False)):
            return True
        try:
            return bool(client.connect())
        except Exception:
            return False

    def _close_stale_clients(self, active_keys: set[str]) -> None:
        with self._client_lock:
            stale = [key for key in self._clients if key not in active_keys]
            for key in stale:
                client = self._clients.pop(key, None)
                self._disconnect_client(client)

    def shutdown(self) -> None:
        with self._client_lock:
            for client in self._clients.values():
                self._disconnect_client(client)
            self._clients.clear()

    def poll(self, payload: dict[str, Any] | None = None, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        module_payload = payload if isinstance(payload, dict) else self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {}) if isinstance(module_payload, dict) else {}
        sensor_config = module_payload.get("sensor_config", []) if isinstance(module_payload, dict) else []
        devices = module_config.get("devices", []) if isinstance(module_config, dict) else []
        active_device_keys: set[str] = set()

        for device in devices if isinstance(devices, list) else []:
            if not isinstance(device, dict) or not device.get("enabled", True):
                continue

            device_id = str(device.get("id") or "device")
            device_key = self._get_device_key(device)
            active_device_keys.add(device_key)
            device_sensors = []
            for sensor in sensor_config if isinstance(sensor_config, list) else []:
                if not isinstance(sensor, dict):
                    continue
                if str(sensor.get("device_id") or "") not in {device_id, str(device.get("id") or "")}:
                    continue
                if not self.should_poll_sensor(sensor, due_sensor_types):
                    continue
                device_sensors.append(sensor)
            if not device_sensors:
                continue

            client = self._ensure_client(device_key, device)
            connected = self._ensure_connected(client)

            for sensor in device_sensors:

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
                    "source_topic": f"poller://mppt/{device_id}",
                }
                self.live_data_store.ingest_sensor(self.module_name, str(sensor.get("name") or sensor.get("address") or "sensor"), sensor_payload)

        self._close_stale_clients(active_device_keys)

        return self.build_snapshot(module_payload)

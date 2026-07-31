from __future__ import annotations

from typing import Any

from .base import BaseModulePoller

try:
    from pymodbus.client import ModbusSerialClient, ModbusTcpClient
except Exception:  # pragma: no cover - optional runtime dependency
    ModbusSerialClient = None
    ModbusTcpClient = None


class ModulePoller(BaseModulePoller):
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

    def poll(self, payload: dict[str, Any] | None = None, due_sensor_types: set[str] | None = None) -> dict[str, Any]:
        module_payload = payload if isinstance(payload, dict) else self.config_manager.get_module_payload(self.module_name)
        module_config = module_payload.get("module_config", {}) if isinstance(module_payload, dict) else {}
        sensor_config = module_payload.get("sensor_config", []) if isinstance(module_payload, dict) else []
        devices = module_config.get("devices", []) if isinstance(module_config, dict) else []

        for device in devices if isinstance(devices, list) else []:
            if not isinstance(device, dict) or not device.get("enabled", True):
                continue

            device_id = str(device.get("id") or "device")
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

            client = self._build_client(device)
            connected = False
            if client is not None:
                try:
                    connected = bool(client.connect())
                except Exception:
                    connected = False

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

            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        return self.build_snapshot(module_payload)

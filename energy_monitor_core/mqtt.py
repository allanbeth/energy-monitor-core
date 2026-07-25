from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

try:
    import paho.mqtt.client as mqtt
except Exception:  # pragma: no cover - handled at runtime
    mqtt = None

from .logging import get_logger


logger = get_logger(__name__)


class MQTTPublisher:
    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.client = None
        self.connected = False
        self._initialise_client()

    def _initialise_client(self) -> None:
        if mqtt is None:
            logger.warning("paho-mqtt is unavailable; MQTT publishing is disabled")
            return

        client_id = str(self.config.get("client_id") or "energy_monitor_core")
        self.client = mqtt.Client(client_id=client_id)
        username = str(self.config.get("username") or "")
        password = str(self.config.get("password") or "")
        if username:
            self.client.username_pw_set(username, password)

        try:
            broker = str(self.config.get("broker") or "localhost")
            port = int(self.config.get("port") or 1883)
            self.client.connect(broker, port, keepalive=60)
            self.client.loop_start()
            self.connected = True
            logger.info("Connected MQTT publisher to %s:%s", broker, port)
        except Exception as error:
            logger.warning("MQTT connection failed: %s", error)
            self.connected = False

    def get_connection_status(self) -> Dict[str, Any]:
        return {"state": "Connected" if self.connected else "Disconnected"}

    def publish_active_modules(self, active_modules: Dict[str, Any], live_data: Dict[str, Any], mqtt_config: Dict[str, Any]) -> None:
        if not self.client or not self.connected:
            return

        base_topic = str((mqtt_config or {}).get("base_topic") or self.config.get("base_topic") or "energy_monitor_core")
        for module_name, module_data in active_modules.items():
            payload = {
                "module": module_name,
                "active": bool(module_data.get("active")),
                "live_data": live_data.get(module_name, {}),
            }
            self.client.publish(f"{base_topic}/{module_name}/status", json.dumps(payload), retain=True)

    def publish_hub_totals(self, aggregate_totals: Dict[str, Any]) -> None:
        if not self.client or not self.connected:
            return

        base_topic = str(self.config.get("base_topic") or "energy_monitor_core")
        self.client.publish(f"{base_topic}/summary", json.dumps(aggregate_totals), retain=True)


class MQTTSubscriber:
    def __init__(self, config: Dict[str, Any], live_data_store: Any):
        self.config = config or {}
        self.live_data_store = live_data_store
        self.client = None
        self.connected = False
        self._initialise_client()

    def _initialise_client(self) -> None:
        if mqtt is None:
            logger.warning("paho-mqtt is unavailable; MQTT ingestion is disabled")
            return

        client_id = f"{str(self.config.get('client_id') or 'energy_monitor_core')}_ingest"
        self.client = mqtt.Client(client_id=client_id)
        username = str(self.config.get("username") or "")
        password = str(self.config.get("password") or "")
        if username:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        try:
            broker = str(self.config.get("broker") or "localhost")
            port = int(self.config.get("port") or 1883)
            self.client.connect(broker, port, keepalive=60)
            self.client.loop_start()
            self.connected = True
            logger.info("Connected MQTT ingestor to %s:%s", broker, port)
        except Exception as error:
            logger.warning("MQTT ingestion connection failed: %s", error)
            self.connected = False

    def _base_topic(self) -> str:
        return str(self.config.get("base_topic") or "energy_monitor_core").strip() or "energy_monitor_core"

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.connected = False
            logger.error("MQTT ingestion connection failed with code %s", rc)
            return

        self.connected = True
        base_topic = self._base_topic()
        client.subscribe(f"{base_topic}/+/sensors/#")
        client.subscribe(f"{base_topic}/+/sensor/#")
        client.subscribe(f"{base_topic}/+/status")
        client.subscribe(f"{base_topic}/+/snapshot")
        logger.info("MQTT ingestor subscribed to %s", base_topic)

    def _parse_topic(self, topic: str) -> Tuple[str, str]:
        parts = [part for part in str(topic or "").split("/") if part]
        base_topic = self._base_topic()
        if len(parts) < 3 or parts[0] != base_topic:
            return "", ""
        module_name = parts[1]
        sensor_name = ""
        if len(parts) >= 4 and parts[2] in {"sensor", "sensors"}:
            sensor_name = "/".join(parts[3:])
        return module_name, sensor_name

    def _decode_payload(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, (bytes, bytearray)):
            raw = payload.decode("utf-8", errors="replace")
        else:
            raw = str(payload or "")
        try:
            parsed = json.loads(raw)
        except Exception:
            return {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"raw": raw}

    def _on_message(self, client, userdata, message):
        try:
            payload = self._decode_payload(message.payload)
            module_name, sensor_name = self._parse_topic(message.topic)
            if not module_name:
                module_name = str(payload.get("module") or "").strip()
            if not module_name:
                return

            if sensor_name:
                sensor_payload = dict(payload)
                sensor_payload.setdefault("name", payload.get("name") or sensor_name)
                sensor_payload.setdefault("source_topic", message.topic)
                self.live_data_store.ingest_sensor(module_name, sensor_name, sensor_payload)
                return

            if isinstance(payload.get("sensors"), (dict, list)):
                self.live_data_store.ingest_module_snapshot(module_name, payload)
                return

            sensor_name = str(payload.get("sensor_name") or payload.get("name") or module_name).strip()
            self.live_data_store.ingest_sensor(module_name, sensor_name, {**payload, "source_topic": message.topic})
        except Exception as error:
            logger.warning("MQTT ingestion failed for %s: %s", getattr(message, "topic", "unknown"), error)

    def get_connection_status(self) -> Dict[str, Any]:
        return {"state": "Connected" if self.connected else "Disconnected"}

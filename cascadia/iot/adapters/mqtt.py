"""
mqtt.py — Cascadia OS v0.47
MQTT sensor adapter for CONDUIT IoT bridge.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from .base import SensorAdapter

logger = logging.getLogger(__name__)


class MQTTAdapter(SensorAdapter):
    """MQTT protocol adapter for CONDUIT. Wraps paho-mqtt with graceful import fallback."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._client = None

    @property
    def name(self) -> str:
        return 'mqtt'

    def start(self, callback: Callable) -> None:
        self._callback = callback
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.warning('CONDUIT: paho-mqtt not installed — MQTT adapter unavailable. '
                           'Install with: pip install paho-mqtt')
            return

        broker_host = self._config.get('broker_host', 'localhost')
        broker_port = int(self._config.get('broker_port', 1883))
        topics = self._config.get('topics', [])
        username = self._config.get('username', '')
        password = self._config.get('password', '')

        client = mqtt.Client()

        if username:
            client.username_pw_set(username, password)

        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                self._connected = True
                logger.info('CONDUIT MQTT connected to %s:%s', broker_host, broker_port)
                for topic in topics:
                    c.subscribe(topic)
                    logger.info('CONDUIT MQTT subscribed to %s', topic)
            else:
                logger.warning('CONDUIT MQTT connection failed rc=%s', rc)

        def on_disconnect(c, userdata, rc):
            self._connected = False
            logger.info('CONDUIT MQTT disconnected rc=%s', rc)

        def on_message(c, userdata, msg):
            try:
                try:
                    payload = json.loads(msg.payload.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = msg.payload.decode('utf-8', errors='replace')

                # Extract device_id from topic (first segment before /)
                topic_parts = msg.topic.split('/')
                device_id = topic_parts[0] if topic_parts else msg.topic

                if self._callback:
                    self._callback(device_id, msg.topic, payload)
            except Exception as exc:
                logger.error('CONDUIT MQTT message processing error: %s', exc)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        try:
            client.connect(broker_host, broker_port, keepalive=60)
            client.loop_start()
            self._client = client
        except Exception as exc:
            logger.error('CONDUIT MQTT connect failed: %s', exc)

    def stop(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as exc:
                logger.error('CONDUIT MQTT stop error: %s', exc)
        self._connected = False
        logger.info('CONDUIT MQTT adapter stopped')

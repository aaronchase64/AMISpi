# SPDX-License-Identifier: GPL-2.0-or-later
"""MQTT 3.1.1 via Paho: reconnects, keepalive, TLS and QoS 1 acknowledgements."""
from __future__ import annotations
import logging
import ssl
import threading
import paho.mqtt.client as paho

log = logging.getLogger('amis.mqtt')

class MqttPublisher:
    def __init__(self, host: str, port: int = 1883, client_id: str = 'amis-pi',
                 username: str = '', password: str = '', tls: bool = False,
                 tls_insecure: bool = False, keepalive: int = 60, status_topic: str = ''):
        self.host, self.port = host, port
        self.keepalive = max(5, keepalive)
        self.status_topic = status_topic
        self._connected = threading.Event()
        self._stopped = threading.Event()
        self._started = False
        self._client = paho.Client(paho.CallbackAPIVersion.VERSION2,
                                  client_id=client_id, protocol=paho.MQTTv311)
        self._client.max_queued_messages_set(10)
        self._client.max_inflight_messages_set(10)
        self._client.reconnect_delay_set(1, 30)
        self._client.connect_timeout = 3.0
        if username:
            self._client.username_pw_set(username, password or None)
        if status_topic:
            self._client.will_set(status_topic, 'offline', qos=1, retain=True)
        if tls:
            ctx = ssl.create_default_context()
            if tls_insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._client.tls_set_context(ctx)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_connect_fail = self._on_connect_fail

    def _on_connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            log.warning('MQTT broker refused connection: %s', reason)
            return
        if self._stopped.is_set():
            client.disconnect()
            return
        if self.status_topic:
            client.publish(self.status_topic, 'online', qos=1, retain=True)
        self._connected.set()
        log.info('MQTT connected to %s:%d', self.host, self.port)

    def _on_connect_fail(self, client, userdata):
        log.warning('MQTT connection to %s:%d failed; reconnecting', self.host, self.port)

    def _on_disconnect(self, client, userdata, flags, reason, properties):
        self._connected.clear()
        if not self._stopped.is_set():
            log.warning('MQTT disconnected: %s; reconnecting', reason)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def wait_connected(self, timeout: float) -> bool:
        return self._connected.wait(timeout)

    def start(self) -> None:
        self._client.connect_async(self.host, self.port, self.keepalive)
        self._client.loop_start()
        self._started = True

    def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> bool:
        if not self.connected or self._stopped.is_set():
            return False
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        # Accepted by the client; Paho tracks PUBACK and retransmission for QoS 1.
        return info.rc == paho.MQTT_ERR_SUCCESS

    def stop(self) -> None:
        if not self._started:
            return
        self._stopped.set()
        try:
            if self.connected and self.status_topic:
                info = self._client.publish(self.status_topic, 'offline', qos=1, retain=True)
                info.wait_for_publish(timeout=2)
        except (RuntimeError, OSError):
            pass
        finally:
            self._client.disconnect()
            self._client.loop_stop()
            self._connected.clear()
            self._started = False

"""Settings and MQTT manager unit tests (no broker, no database)."""

from __future__ import annotations

from app.core.config import Settings
from app.mqtt.client import MqttClientManager


def test_default_topic_subscription_and_prefix() -> None:
    s = Settings()
    assert s.mqtt_topic_prefix == "bms"
    assert s.mqtt_telemetry_topic == "bms/+/telemetry"
    assert s.mqtt_qos == 1


def test_api_prefix_default() -> None:
    assert Settings().api_prefix == "/api/v1"


def test_mqtt_status_shape_when_idle() -> None:
    manager = MqttClientManager(Settings())
    status = manager.status()
    assert status["connected"] is False
    assert status["reconnects"] == 0
    assert status["messages_received"] == 0
    assert status["connected_since"] is None
    assert status["last_error"] is None


def test_mqtt_reconnect_bounds_are_sane() -> None:
    s = Settings()
    assert 0 < s.mqtt_reconnect_min_s <= s.mqtt_reconnect_max_s

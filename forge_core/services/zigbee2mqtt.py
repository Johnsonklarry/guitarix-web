"""Parse Zigbee2MQTT MQTT messages into :class:`ZigbeeDevice` snapshots.

Zigbee2MQTT publishes one JSON object per device to the topic
``zigbee2mqtt/<friendly_name>`` (subtopics such as ``/availability`` are
also tolerated).  :class:`Zigbee2MQTT` turns those messages into a plain
:class:`ZigbeeDevice` value object whose decoded payload keys are reachable
both as a mapping (``device.properties["battery"]``) and as attributes
(``device.battery``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


class Zigbee2MQTTError(ValueError):
    """Raised when a topic or payload cannot be parsed."""


#: Default temperature (degrees Celsius) at or below which a freeze alert fires.
DEFAULT_FREEZE_THRESHOLD = 0.0

#: Default window (seconds) during which an identical alert is suppressed.
DEFAULT_DEBOUNCE_SECONDS = 300.0

#: Plain-language shutoff instruction for a detected water leak.
WATER_LEAK_INSTRUCTION = (
    "Water leak detected: shut off the water supply immediately and check "
    "the area for damage."
)

#: Plain-language shutoff instruction for a detected freeze condition.
FREEZE_INSTRUCTION = (
    "Freeze risk detected: shut off the water supply to prevent burst pipes "
    "and protect the affected area."
)


@dataclass
class ZigbeeDevice:
    """A single Zigbee2MQTT device state snapshot.

    ``device_id`` is the friendly name taken from the topic, ``topic`` is the
    full MQTT topic, and ``properties`` holds the decoded JSON object.  Keys in
    ``properties`` are also exposed as attributes, so a payload of
    ``{"battery": 95, "state": "ON"}`` supports both ``device.battery`` and
    ``device["state"]``.
    """

    device_id: str
    topic: str
    properties: dict = field(default_factory=dict)
    alert: str | None = None
    alert_type: str | None = None
    alert_instruction: str | None = None

    def __getattr__(self, name: str) -> Any:
        # Dataclass fields resolve through normal lookup; only unknown names
        # fall through to the decoded payload.
        try:
            return self.__dict__["properties"][name]
        except (KeyError, TypeError):
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        """Return a payload value, like :meth:`dict.get`."""
        return self.properties.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.properties


class Zigbee2MQTT:
    """Parse messages published by a Zigbee2MQTT bridge."""

    def __init__(
        self,
        base_topic: str = "zigbee2mqtt",
        freeze_threshold: float = DEFAULT_FREEZE_THRESHOLD,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        if not isinstance(base_topic, str):
            raise TypeError("base_topic must be a string")
        normalized = base_topic.strip("/")
        if not normalized:
            raise ValueError("base_topic must not be empty")
        self.base_topic = normalized
        self.freeze_threshold = freeze_threshold
        self.debounce_seconds = debounce_seconds
        # device_id -> (alert_type, monotonic timestamp of last report)
        self._last_alert: dict[str, tuple[str, float]] = {}

    def _evaluate_alert(self, device: ZigbeeDevice) -> None:
        """Set alert fields on ``device`` for leak/freeze conditions.

        ``water_leak`` truthy values take precedence over low temperatures.
        The alert is suppressed when an identical alert type was already
        reported for this device within ``debounce_seconds``.
        """
        alert_type: str | None = None
        instruction: str | None = None

        if device.get("water_leak"):
            alert_type = "water_leak"
            instruction = WATER_LEAK_INSTRUCTION
        else:
            temperature = device.get("temperature")
            if isinstance(temperature, (int, float)) and not isinstance(
                temperature, bool
            ):
                if temperature <= self.freeze_threshold:
                    alert_type = "freeze"
                    instruction = FREEZE_INSTRUCTION

        if alert_type is None:
            return

        now = time.monotonic()
        previous = self._last_alert.get(device.device_id)
        if previous is not None:
            previous_type, previous_time = previous
            if (
                previous_type == alert_type
                and now - previous_time < self.debounce_seconds
            ):
                return

        self._last_alert[device.device_id] = (alert_type, now)
        device.alert = alert_type
        device.alert_type = alert_type
        device.alert_instruction = instruction

    def parse_message(self, topic: str, payload: bytes) -> ZigbeeDevice:
        """Parse one ``topic``/``payload`` pair into a :class:`ZigbeeDevice`.

        ``topic`` must sit below the configured base topic.  ``payload`` is the
        raw MQTT body; JSON objects are decoded as-is, empty payloads become an
        empty mapping, and non-JSON bodies (for example availability strings)
        are stored under the ``value`` key.
        """
        if not isinstance(topic, str):
            raise TypeError("topic must be a string")
        if isinstance(payload, (bytearray, memoryview)):
            payload = bytes(payload)
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")

        prefix = self.base_topic + "/"
        if not topic.startswith(prefix):
            raise Zigbee2MQTTError(
                "topic %r is not below %r" % (topic, self.base_topic)
            )
        remainder = topic[len(prefix):].strip("/")
        device_id, _, _ = remainder.partition("/")
        if not device_id:
            raise Zigbee2MQTTError("topic %r does not name a device" % (topic,))

        text = payload.decode("utf-8").strip()
        if not text:
            data: Any = {}
        else:
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, RecursionError):
                data = {"value": text}
        if not isinstance(data, dict):
            data = {"value": data}
        device = ZigbeeDevice(device_id=device_id, topic=topic, properties=data)
        self._evaluate_alert(device)
        return device

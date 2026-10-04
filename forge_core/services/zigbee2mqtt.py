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
from dataclasses import dataclass, field
from typing import Any


class Zigbee2MQTTError(ValueError):
    """Raised when a topic or payload cannot be parsed."""


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

    def __init__(self, base_topic: str = "zigbee2mqtt") -> None:
        if not isinstance(base_topic, str):
            raise TypeError("base_topic must be a string")
        normalized = base_topic.strip("/")
        if not normalized:
            raise ValueError("base_topic must not be empty")
        self.base_topic = normalized

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
        return ZigbeeDevice(device_id=device_id, topic=topic, properties=data)

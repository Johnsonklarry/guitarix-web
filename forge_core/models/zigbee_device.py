from dataclasses import dataclass

@dataclass
class ZigbeeDevice:
    device_id: str
    friendly_name: str
    status: str
    last_seen: str
    battery: int
    temperature: float
    humidity: float

"""
Sentinel Message Packet (SMP) protocol.
All inter-agent communication flows through this format.
"""

import uuid
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Sender(str, Enum):
    INGEST = "MOD-INGEST"
    PARSE = "MOD-PARSE"
    MATCH = "MOD-MATCH"
    QA = "QA-AGENT"
    ORCHESTRATOR = "ORCHESTRATOR"


class PayloadType(str, Enum):
    RAW_HTML = "RAW_HTML"
    JSON_JOB = "JSON_JOB"
    VECTOR_SCORE = "VECTOR_SCORE"
    ERROR_LOG = "ERROR_LOG"
    COMMAND = "COMMAND"


class Priority(str, Enum):
    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class SentinelPacket:
    sender: Sender
    payload_type: PayloadType
    payload: dict = field(default_factory=dict)
    priority: Priority = Priority.MED
    packet_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        d = asdict(self)
        d["sender"] = self.sender.value
        d["payload_type"] = self.payload_type.value
        d["priority"] = self.priority.value
        return json.dumps(d, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "SentinelPacket":
        return cls(
            sender=Sender(d["sender"]),
            payload_type=PayloadType(d["payload_type"]),
            payload=d.get("payload", {}),
            priority=Priority(d.get("priority", "MED")),
            packet_id=d.get("packet_id", str(uuid.uuid4())),
            trace_id=d.get("trace_id", str(uuid.uuid4())),
            timestamp=d.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )

"""
Shared pytest fixtures + path setup for the Local Recruiting Ops backend test suite.

The application modules import each other as ``core.x`` / ``agents.x``
(see main.py, server.py, orchestrator.py). pytest's rootdir is
``lro/api`` via pytest.ini, but rootdir alone doesn't put that dir
on ``sys.path`` — so we insert it here, at import time, before any test
module's ``import core...`` line runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

# tests/ -> lro/api
_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

import pytest  # noqa: E402  (must come after sys.path is fixed)


# ──────────────────────────────────────────────────────────────────
# Packet helper — QAAgent.validate / MatchAgent both speak SentinelPacket
# ──────────────────────────────────────────────────────────────────
@pytest.fixture
def make_packet():
    """Factory that builds a JSON_JOB SentinelPacket from a payload dict.

    Usage:
        pkt = make_packet({"title": "PM", "company": "Acme"})
        pkt = make_packet({...}, payload_type=PayloadType.RAW_HTML)
    """
    from core.protocol import SentinelPacket, Sender, PayloadType

    def _make(payload: dict, *, payload_type=PayloadType.JSON_JOB,
              sender=Sender.PARSE):
        return SentinelPacket(
            sender=sender,
            payload_type=payload_type,
            payload=dict(payload),
        )

    return _make

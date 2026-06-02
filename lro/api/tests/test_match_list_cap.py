"""Tests for match list cap demotion."""
from core.protocol import SentinelPacket, PayloadType, Sender, Priority
from agents.match import MatchAgent


def _pkt(score: float, tier: str) -> SentinelPacket:
    return SentinelPacket(
        sender=Sender.MATCH,
        payload_type=PayloadType.VECTOR_SCORE,
        payload={
            "title": f"Role {score}",
            "company": "Co",
            "url": f"https://example.com/{score}",
            "_match_score": score,
            "_match_tier": tier,
            "_is_match": tier == "match",
        },
        priority=Priority.HIGH,
    )


def test_apply_match_list_cap_demotes_excess():
    agent = MatchAgent({"match_list_cap": 2, "preferences": {}})
    results = [
        _pkt(0.9, "match"),
        _pkt(0.8, "match"),
        _pkt(0.7, "match"),
        _pkt(0.5, "maybe"),
    ]
    capped, n = agent.apply_match_list_cap(results)
    assert n == 1
    tiers = [p.payload["_match_tier"] for p in capped]
    assert tiers.count("match") == 2
    assert capped[2].payload["_match_tier"] == "maybe"
    assert capped[2].payload.get("_match_list_cap_demoted") is True


def test_apply_match_list_cap_noop_when_disabled():
    agent = MatchAgent({"match_list_cap": 0, "preferences": {}})
    results = [_pkt(0.9, "match"), _pkt(0.8, "match")]
    capped, n = agent.apply_match_list_cap(results)
    assert n == 0
    assert all(p.payload["_match_tier"] == "match" for p in capped)

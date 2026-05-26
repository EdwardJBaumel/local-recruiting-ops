"""Tests for Ollama availability helpers (stage skip when models not pulled)."""
from __future__ import annotations

from core import llm
from core.protocol import PayloadType, Sender
from agents.parse import ParseAgent
from agents.archetype import classify_archetype
from agents.analyzer import FitGapAnalyzer


def test_task_llm_ready_false_when_tags_empty(monkeypatch):
    monkeypatch.setattr(llm, "_list_available_models", lambda: [])
    assert llm.task_llm_ready("parse", explicit_model="qwen3:8b") is False


def test_task_llm_ready_true_when_model_pulled(monkeypatch):
    monkeypatch.setattr(llm, "_list_available_models", lambda: ["qwen3:8b"])
    assert llm.task_llm_ready("parse", explicit_model="qwen3:8b") is True


def test_task_llm_ready_uses_fallback_chain(monkeypatch):
    monkeypatch.setattr(llm, "_list_available_models", lambda: ["qwen3:4b"])
    monkeypatch.setattr(llm, "_missing_models", {"qwen3:8b"})
    assert llm.task_llm_ready("parse", explicit_model="qwen3:8b") is True


def test_parse_run_skips_html_when_no_model(monkeypatch, make_packet):
    monkeypatch.setattr(llm, "task_llm_ready", lambda *a, **k: False)
    agent = ParseAgent({"model": "qwen3:8b"})
    pkt = make_packet(
        {"html": "<div>PM</div>", "source_url": "https://example.com"},
        payload_type=PayloadType.RAW_HTML,
        sender=Sender.INGEST,
    )
    assert agent.run([pkt]) == []


def test_archetype_regex_works_without_llm(monkeypatch):
    monkeypatch.setattr(llm, "task_llm_ready", lambda *a, **k: False)
    result = classify_archetype("Senior Product Manager", "")
    assert result["archetype"] == "pm"


def test_archetype_ambiguous_skips_llm_when_no_model(monkeypatch):
    monkeypatch.setattr(llm, "task_llm_ready", lambda *a, **k: False)
    result = classify_archetype("Role 4821", "Generic responsibilities.")
    assert result["archetype"] == "unclassified"
    assert "not pulled" in result["rationale"]


def test_analyzer_skips_when_no_model(monkeypatch, make_packet):
    monkeypatch.setattr(llm, "task_llm_ready", lambda *a, **k: False)
    analyzer = FitGapAnalyzer({"profile_text": "PM with 5 years", "model": "qwen3:8b"})
    pkt = make_packet({"title": "PM", "company": "Acme", "_is_match": True})
    pkt.payload["_is_match"] = True
    assert analyzer.run([pkt]) == []

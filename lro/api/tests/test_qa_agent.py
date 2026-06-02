"""
Unit tests for agents.qa.QAAgent.validate().

title + company are REQUIRED (missing -> invalid / counts as failed).
location / description / technologies / seniority are DESIRED
(missing -> still valid, but warned).
"""
import pytest

from agents.qa import QAAgent, REQUIRED_FIELDS, DESIRED_FIELDS
from core.protocol import PayloadType


def _valid_payload(**overrides):
    base = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "location": "Remote",
        "description": "Own the roadmap for the platform team.",
        "technologies": ["sql", "figma"],
        "seniority": "senior",
    }
    base.update(overrides)
    return base


def test_required_and_desired_field_lists():
    # Guard the contract these tests depend on.
    assert set(REQUIRED_FIELDS) == {"title", "company"}
    assert set(DESIRED_FIELDS) == {"location", "description", "technologies", "seniority"}


def test_validate_rejects_non_json_job_packet(make_packet):
    pkt = make_packet(_valid_payload(), payload_type=PayloadType.RAW_HTML)
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is False
    assert issues == ["Not a JSON_JOB packet"]


def test_validate_fully_populated_packet_passes_clean(make_packet):
    pkt = make_packet(_valid_payload())
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is True
    assert issues == []


# ── required fields ───────────────────────────────────────────────
def test_missing_title_is_invalid(make_packet):
    pkt = make_packet(_valid_payload(title=None))
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is False
    assert any("MISSING_REQUIRED: title" in i for i in issues)


def test_missing_company_is_invalid(make_packet):
    pkt = make_packet(_valid_payload(company=None))
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is False
    assert any("MISSING_REQUIRED: company" in i for i in issues)


def test_blank_string_required_field_is_invalid(make_packet):
    # Whitespace-only counts as missing.
    pkt = make_packet(_valid_payload(company="   "))
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is False
    assert any("MISSING_REQUIRED: company" in i for i in issues)


def test_both_required_missing_is_invalid(make_packet):
    pkt = make_packet(_valid_payload(title=None, company=None))
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is False
    assert any("MISSING_REQUIRED: title" in i for i in issues)
    assert any("MISSING_REQUIRED: company" in i for i in issues)


# ── desired fields — missing keeps the packet valid ───────────────
@pytest.mark.parametrize("field", ["location", "description", "technologies", "seniority"])
def test_missing_desired_field_still_valid_but_warned(make_packet, field):
    pkt = make_packet(_valid_payload(**{field: None}))
    is_valid, issues = QAAgent().validate(pkt)
    # Desired field missing → STILL valid (no critical issue).
    assert is_valid is True
    # ...but it is flagged as a desired-field warning.
    assert any(f"MISSING_DESIRED: {field}" in i for i in issues)


def test_missing_all_desired_fields_still_valid(make_packet):
    pkt = make_packet(_valid_payload(
        location=None, description=None, technologies=None, seniority=None,
    ))
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is True
    assert len([i for i in issues if "MISSING_DESIRED" in i]) == 4


# ── bogus-data heuristics ─────────────────────────────────────────
def test_too_short_title_flagged_as_suspect(make_packet):
    pkt = make_packet(_valid_payload(title="PM"))  # len 2 < 3
    is_valid, issues = QAAgent().validate(pkt)
    # Still valid (title is present), but flagged suspect.
    assert is_valid is True
    assert any("SUSPECT: title too short" in i for i in issues)


def test_too_long_title_flagged_as_suspect(make_packet):
    pkt = make_packet(_valid_payload(title="x" * 201))
    is_valid, issues = QAAgent().validate(pkt)
    assert is_valid is True
    assert any("SUSPECT: title too long" in i for i in issues)


# ── run() roll-up ─────────────────────────────────────────────────
def test_run_splits_valid_and_error_packets(make_packet):
    good = make_packet(_valid_payload())
    warned = make_packet(_valid_payload(location=None))  # desired missing
    bad = make_packet(_valid_payload(company=None))      # required missing

    agent = QAAgent()
    valid, errors = agent.run([good, warned, bad])

    assert len(valid) == 2          # good + warned both pass to matching
    assert len(errors) == 1         # bad becomes an ERROR_LOG packet
    assert errors[0].payload_type == PayloadType.ERROR_LOG
    assert agent.stats["passed"] == 1
    assert agent.stats["warned"] == 1
    assert agent.stats["failed"] == 1


def test_run_ignores_non_job_packets(make_packet):
    html = make_packet(_valid_payload(), payload_type=PayloadType.RAW_HTML)
    agent = QAAgent()
    valid, errors = agent.run([html])
    # Non-JSON_JOB packets aren't QA'd at all.
    assert valid == []
    assert errors == []

"""
Unit tests for core.salary_extract.extract_salary().

Pure regex extraction — no network, no LLM. Covers the cases the
docstring + caller (agents.ingest._make_packet) actually rely on:
$-symbol ranges, K-suffix, currency-code ranges, dash variants,
hourly rejection, floor-only, the $20k-$2M plausibility band, and
the HTML-vs-plaintext contract (caller strips HTML first).
"""
from core.salary_extract import extract_salary, _norm_amount


# ── $-symbol ranges ───────────────────────────────────────────────
def test_dollar_range_with_commas():
    out = extract_salary("The band is $140,000 - $180,000 per year.")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


def test_dollar_range_no_spaces():
    out = extract_salary("Comp: $140,000-$180,000.")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


def test_dollar_range_k_suffix():
    out = extract_salary("Range $140K-$180K depending on level.")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


def test_dollar_range_lowercase_k():
    out = extract_salary("$140k - $180k")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


def test_low_high_swapped_is_corrected():
    # Extractor swaps lo/hi when stated high-to-low.
    out = extract_salary("$180,000 - $140,000")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


# ── dash variants ─────────────────────────────────────────────────
def test_en_dash_separator():
    out = extract_salary("$140,000 – $180,000")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


def test_em_dash_separator():
    out = extract_salary("$140,000 — $180,000")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


def test_to_separator():
    out = extract_salary("$140,000 to $180,000")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


# ── currency-code ranges (USD / GBP / EUR) ────────────────────────
def test_usd_code_range():
    out = extract_salary("USD 140,000 - 180,000")
    assert out == {"min": 140000, "max": 180000, "currency": "USD"}


def test_gbp_code_range():
    out = extract_salary("GBP 80,000 - 120,000")
    assert out == {"min": 80000, "max": 120000, "currency": "GBP"}


def test_eur_code_range():
    out = extract_salary("EUR 90,000 - 120,000")
    assert out == {"min": 90000, "max": 120000, "currency": "EUR"}


def test_pound_symbol_range():
    out = extract_salary("£80,000-£120,000")
    assert out == {"min": 80000, "max": 120000, "currency": "GBP"}


def test_euro_symbol_range():
    out = extract_salary("€90,000 – €120,000")
    assert out == {"min": 90000, "max": 120000, "currency": "EUR"}


# ── hourly-rate rejection ─────────────────────────────────────────
def test_hourly_rate_rejected_slash_hour():
    assert extract_salary("$45/hour, flexible schedule") is None


def test_hourly_rate_rejected_per_hour():
    assert extract_salary("$45 - $60 per hour") is None


def test_hourly_noise_near_range_drops_match():
    # An annual-looking range sitting within ~30 chars of an hourly
    # token is dropped (the _looks_hourly window check).
    assert extract_salary("$40,000-$60,000 /hr contract") is None


# ── floor-only ("starting at" / "from") ───────────────────────────
def test_floor_only_starting_at():
    out = extract_salary("Starting at $140,000 plus equity.")
    assert out == {"min": 140000, "max": None, "currency": "USD"}


def test_floor_only_from_k_suffix():
    out = extract_salary("From $150k, negotiable.")
    assert out == {"min": 150000, "max": None, "currency": "USD"}


# ── plausibility band ($20k - $2M) ────────────────────────────────
def test_below_floor_band_rejected():
    # $5,000-$8,000 is below the $20k floor and both look like
    # standalone small numbers (k-less, >999), so they're dropped.
    assert extract_salary("$5,000 - $8,000") is None


def test_above_ceiling_band_rejected():
    # $3,000,000 exceeds the $2M ceiling.
    assert extract_salary("$3,000,000 - $4,000,000") is None


def test_norm_amount_promotes_bare_hundreds():
    # "140" with no k-suffix is read as 140k by _norm_amount.
    assert _norm_amount("140", False) == 140000


def test_norm_amount_rejects_implausible():
    # 5 alone -> 5000 still below 20k floor -> None.
    assert _norm_amount("5", False) is None
    # 9_999_999 way over ceiling -> None.
    assert _norm_amount("9999999", False) is None


def test_norm_amount_k_suffix_multiplies():
    assert _norm_amount("180", True) == 180000


# ── spread sanity (6x cap) ────────────────────────────────────────
def test_absurd_spread_rejected():
    # 30000..400000 is >6x — likely captured unrelated digits.
    assert extract_salary("$30,000 - $400,000") is None


# ── HTML vs plain text ────────────────────────────────────────────
def test_html_entity_encoded_input_returns_none():
    # The caller (ingest._make_packet) strips HTML + decodes entities
    # BEFORE calling. Raw entity-encoded HTML has &#36; / &mdash; that
    # the plain-text regex can't see, so it must return None.
    html = "<p>Compensation: &#36;140,000 &mdash; &#36;180,000</p>"
    assert extract_salary(html) is None


def test_plaintext_equivalent_of_that_html_parses():
    # Same content, post-strip: the regex finds it.
    plain = "Compensation: $140,000 - $180,000"
    assert extract_salary(plain) == {
        "min": 140000, "max": 180000, "currency": "USD",
    }


# ── empty / non-string guards ─────────────────────────────────────
def test_empty_string_returns_none():
    assert extract_salary("") is None


def test_none_returns_none():
    assert extract_salary(None) is None


def test_non_string_returns_none():
    assert extract_salary(12345) is None


def test_text_with_no_salary_returns_none():
    assert extract_salary("We are hiring a Senior Product Manager.") is None

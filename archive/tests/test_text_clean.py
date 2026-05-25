"""Tests for the parse text-clean pipeline.

The real-world motivator: a Databricks LinkedIn card came in wrapped
in `<p data-pm-slice>` attributes with a recruiter tracking code
("RDQ226R484") at the top. Regression test for that exact case lives
in TestRealWorldDatabricks.
"""
import pytest

from sentinel.core import text_clean as tc


# ─── strip_html ───────────────────────────────────────────────────
class TestStripHtml:
    def test_empty_returns_empty(self):
        assert tc.strip_html("") == ""
        assert tc.strip_html(None) == ""

    def test_plain_text_preserved(self):
        out = tc.strip_html("hello world")
        assert "hello world" in out

    def test_simple_tag_stripped(self):
        out = tc.strip_html("<p>hello <strong>world</strong></p>")
        assert "hello" in out and "world" in out
        assert "<p>" not in out and "<strong>" not in out

    def test_script_contents_removed(self):
        html = "<p>visible</p><script>alert('evil')</script>"
        out = tc.strip_html(html)
        assert "visible" in out
        assert "alert" not in out
        assert "evil" not in out

    def test_style_contents_removed(self):
        html = "<p>visible</p><style>.x { color: red; }</style>"
        out = tc.strip_html(html)
        assert "visible" in out
        assert "color: red" not in out

    def test_nav_and_footer_removed(self):
        html = "<nav>menu</nav><main>body</main><footer>copyright</footer>"
        out = tc.strip_html(html)
        assert "body" in out
        assert "menu" not in out
        assert "copyright" not in out

    def test_data_attributes_stripped(self):
        html = '<p data-pm-slice="1 1 []">hello</p>'
        out = tc.strip_html(html)
        assert "data-pm-slice" not in out
        assert "hello" in out

    def test_preserves_block_breaks(self):
        html = "<p>first</p><p>second</p>"
        out = tc.strip_html(html)
        # Should contain at least one newline between the two
        # paragraphs so the LLM sees structure.
        assert "first" in out and "second" in out
        assert "\n" in out


# ─── drop_tracking_codes ──────────────────────────────────────────
class TestDropTrackingCodes:
    def test_removes_canonical_code(self):
        out = tc.drop_tracking_codes("RDQ226R484\nSenior PM at Acme")
        assert "RDQ226R484" not in out
        assert "Senior PM at Acme" in out

    def test_removes_req_code(self):
        out = tc.drop_tracking_codes("REQ12345\nbody")
        assert "REQ12345" not in out
        assert "body" in out

    def test_keeps_normal_uppercase_words(self):
        # "LOVED" and similar common words must not trigger the code filter.
        out = tc.drop_tracking_codes("LOVED working here\nWe ARE hiring")
        assert "LOVED" in out
        assert "ARE" in out

    def test_keeps_acronyms_within_sentences(self):
        out = tc.drop_tracking_codes("Work with AWS and GCP daily")
        assert "AWS" in out
        assert "GCP" in out

    def test_empty_stays_empty(self):
        assert tc.drop_tracking_codes("") == ""


# ─── collapse_whitespace ──────────────────────────────────────────
class TestCollapseWhitespace:
    def test_runs_of_spaces_collapsed(self):
        assert tc.collapse_whitespace("a   b\t\tc") == "a b c"

    def test_excessive_newlines_capped(self):
        assert tc.collapse_whitespace("a\n\n\n\n\nb") == "a\n\nb"

    def test_crlf_normalised(self):
        assert tc.collapse_whitespace("a\r\nb") == "a\nb"

    def test_nbsp_collapsed(self):
        assert tc.collapse_whitespace("a\u00a0\u00a0b") == "a b"

    def test_trims_outer_whitespace(self):
        assert tc.collapse_whitespace("   a   ") == "a"


# ─── clean_for_llm ────────────────────────────────────────────────
class TestCleanForLlm:
    def test_pipeline_end_to_end(self):
        html = """
        <nav>menu</nav>
        <p data-pm-slice="1 1 []">RDQ226R484</p>
        <p><strong>Build the Future of Data</strong></p>
        <p>At Databricks we are delivering.</p>
        <script>alert(1)</script>
        <footer>copyright</footer>
        """
        out = tc.clean_for_llm(html)
        assert "RDQ226R484" not in out
        assert "menu" not in out
        assert "alert" not in out
        assert "copyright" not in out
        assert "Build the Future of Data" in out
        assert "Databricks" in out

    def test_truncates_to_budget(self):
        out = tc.clean_for_llm("<p>" + ("x" * 20000) + "</p>", max_chars=1000)
        assert len(out) <= 1000

    def test_empty_html(self):
        assert tc.clean_for_llm("") == ""


# ─── clean_field ──────────────────────────────────────────────────
class TestCleanField:
    def test_unescapes_entities(self):
        assert tc.clean_field("Data &amp; AI") == "Data & AI"

    def test_strips_stray_tags(self):
        assert tc.clean_field("<strong>Senior PM</strong>") == "Senior PM"

    def test_trims_whitespace(self):
        assert tc.clean_field("   hello   ") == "hello"

    def test_empty_becomes_none(self):
        assert tc.clean_field("   ") is None
        assert tc.clean_field("") is None

    def test_non_string_passes_through(self):
        assert tc.clean_field(None) is None
        assert tc.clean_field(42) == 42
        assert tc.clean_field(["x"]) == ["x"]


# ─── is_tracking_code ─────────────────────────────────────────────
class TestIsTrackingCode:
    def test_canonical_code_detected(self):
        assert tc.is_tracking_code("RDQ226R484") is True

    def test_req_prefix_detected(self):
        assert tc.is_tracking_code("REQ12345") is True

    def test_plain_title_not_flagged(self):
        assert tc.is_tracking_code("Senior Product Manager") is False

    def test_acronym_alone_not_flagged(self):
        # "AWS" has no digits, can't be a tracking code.
        assert tc.is_tracking_code("AWS") is False

    def test_non_string_not_flagged(self):
        assert tc.is_tracking_code(None) is False
        assert tc.is_tracking_code(42) is False


# ─── sanitise_job ─────────────────────────────────────────────────
class TestSanitiseJob:
    def test_non_dict_returns_input(self):
        assert tc.sanitise_job(None) is None
        assert tc.sanitise_job("x") == "x"

    def test_entities_unescaped(self):
        job = {"title": "Data &amp; AI Lead"}
        assert tc.sanitise_job(job)["title"] == "Data & AI Lead"

    def test_stray_tags_stripped(self):
        job = {"description": "<p>build <b>cool</b> stuff</p>"}
        out = tc.sanitise_job(job)
        assert out["description"] == "build cool stuff"

    def test_tracking_code_title_nulled(self):
        job = {"title": "RDQ226R484", "company": "Databricks"}
        out = tc.sanitise_job(job)
        assert out["title"] is None
        assert out["company"] == "Databricks"

    def test_tracking_code_company_nulled(self):
        job = {"title": "Senior PM", "company": "REQ9999"}
        out = tc.sanitise_job(job)
        assert out["title"] == "Senior PM"
        assert out["company"] is None

    def test_seniority_lowercased(self):
        job = {"seniority": "Senior"}
        assert tc.sanitise_job(job)["seniority"] == "senior"

    def test_technologies_deduped(self):
        job = {"technologies": ["Python", "python", "SQL", "", None, "sql", "AWS"]}
        out = tc.sanitise_job(job)
        assert out["technologies"] == ["Python", "SQL", "AWS"]

    def test_technologies_keeps_first_casing(self):
        job = {"technologies": ["python", "Python"]}
        out = tc.sanitise_job(job)
        assert out["technologies"] == ["python"]

    def test_input_not_mutated(self):
        job = {"title": "<p>Senior PM</p>", "technologies": ["Py", "Py"]}
        original = {"title": job["title"], "technologies": list(job["technologies"])}
        tc.sanitise_job(job)
        assert job == original


# ─── real-world regression ────────────────────────────────────────
class TestRealWorldDatabricks:
    """The exact shape that triggered the bug report."""

    def test_full_pipeline_on_databricks_card(self):
        html = (
            '<p data-pm-slice="1 1 []">RDQ226R484</p>\n'
            '<p><strong>Build the Future of Data &amp; AI with Databricks SQL</strong></p>\n'
            '<p>At Databricks, we are delivering Data &amp; AI for enterprises '
            '&amp; startups around the world. Our <strong>Databricks SQL (DBSQL) '
            'product</strong> is at the forefront of <strong>next-gen AI '
            'powered cloud data warehousing</strong>, helping businesses query, '
            'visualize, and drive real-time decisions at scale. DBSQL is the '
            'fastest-growing data warehouse in the world.</p>'
        )
        cleaned = tc.clean_for_llm(html)
        # Tracking code gone.
        assert "RDQ226R484" not in cleaned
        # HTML wrapper and data attribute gone.
        assert "data-pm-slice" not in cleaned
        assert "<p>" not in cleaned
        assert "<strong>" not in cleaned
        # Title content present.
        assert "Build the Future of Data" in cleaned
        # Entity unescaped.
        assert "Data & AI" in cleaned
        assert "&amp;" not in cleaned

    def test_sanitise_strips_tracking_title(self):
        job = {
            "title": "RDQ226R484",
            "company": "Databricks",
            "description": "Build the Future of Data &amp; AI",
        }
        out = tc.sanitise_job(job)
        assert out["title"] is None
        assert out["description"] == "Build the Future of Data & AI"

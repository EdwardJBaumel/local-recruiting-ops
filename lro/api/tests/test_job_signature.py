from core.job_signature import build_job_signature, attach_job_signature


def test_build_job_signature_compact():
    payload = {
        "title": "Senior Product Manager, Platform",
        "company": "Stripe",
        "location": "San Francisco, CA",
        "seniority": "senior",
        "remote": "hybrid",
        "technologies": ["kubernetes", "python", "api"],
        "description": (
            "<p>About Stripe we are great.</p>"
            "<h3>What you'll do</h3>"
            "<p>Own the developer platform roadmap and partner with infra teams.</p>"
            "<h3>Requirements</h3>"
            "<p>5+ years PM experience with platform or devtools products.</p>"
        ),
    }
    sig = build_job_signature(payload, max_chars=600)
    assert len(sig) <= 600
    assert "Senior Product Manager" in sig
    assert "Stripe" in sig
    assert "kubernetes" in sig
    assert "About Stripe" not in sig
    assert "developer platform" in sig.lower()


def test_attach_job_signature_mutates_payload():
    payload = {"title": "TPM", "company": "Google", "description": "Program management."}
    attach_job_signature(payload)
    assert "job_signature" in payload
    assert "TPM" in payload["job_signature"]

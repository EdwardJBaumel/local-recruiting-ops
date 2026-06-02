from core.cross_encoder_rerank import normalize_rerank_scores, CrossEncoderReranker


def test_normalize_rerank_scores_spreads_batch():
    raw = [1.0, 2.0, 3.0, 4.0]
    norm = normalize_rerank_scores(raw)
    assert norm[0] == 0.0
    assert norm[-1] == 1.0
    assert len(norm) == 4


def test_normalize_rerank_scores_flat_uses_sigmoid():
    raw = [0.5, 0.5, 0.5]
    norm = normalize_rerank_scores(raw)
    assert all(0.0 < v < 1.0 for v in norm)


def test_blend_score_weighted():
    r = CrossEncoderReranker(blend_weight=0.6, enabled=False)
    blended = r.blend_score(0.40, 0.80)
    assert blended == round(0.4 * 0.4 + 0.6 * 0.8, 4)


def test_document_from_payload_prefers_signature():
    doc = CrossEncoderReranker.document_from_payload({
        "job_signature": "PM @ Acme | Stack: python",
        "description": "x" * 5000,
    })
    assert doc == "PM @ Acme | Stack: python"

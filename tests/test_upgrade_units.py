import json
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.agent.evidence import parse_paragraph, record_sections
from app.rag.retrieval import reciprocal_rank_fusion
from app.routing import IntentRoute, enforce_explicit_evidence_request, tool_arguments
from evals.pdf_fixture import make_pdf
from evals.scoring import contains_fact, fact_matches, retrieval_metrics, score_case


@pytest.mark.parametrize("prompt", ["Explain progress", "Cite my course PDF", "Omega notation", "private methods"])
def test_text_never_overrides_a_refusal(prompt: str) -> None:
    route = IntentRoute(mode="refuse", reason="Safety boundary", risk_flags=["identity_override"])
    assert enforce_explicit_evidence_request(route, prompt).mode == "refuse"


@pytest.mark.parametrize("prompt", ["Explain progress", "What is a grade?", "private methods"])
def test_text_never_authorizes_writes(prompt: str) -> None:
    route = IntentRoute(mode="direct_answer", reason="General knowledge")
    assert not enforce_explicit_evidence_request(route, prompt).mutates_state


def test_rank_fusion_deduplicates_each_list() -> None:
    assert reciprocal_rank_fusion([["a", "a", "b"], ["b", "c"]], 2) == ["b", "a"]


def test_real_pdf_has_extractable_pages() -> None:
    reader = PdfReader(BytesIO(make_pdf(["HTML structures documents.", "Flexbox is one-dimensional."])))
    assert len(reader.pages) == 2
    assert "Flexbox" in reader.pages[1].extract_text()


def test_stable_source_reference() -> None:
    sources = {"stable-id": {"source_id": "stable-id", "excerpt": "Fact"}}
    rendered, citations = parse_paragraph(json.dumps({"text": "Fact", "source_ids": ["stable-id"]}), sources)
    assert "[Source stable-id]" in rendered and citations == list(sources.values())
    with pytest.raises(ValueError):
        parse_paragraph('{"text":"Fact","source_ids":["invented"]}', sources)


def test_fact_match_requires_subject_metric_and_value() -> None:
    fact = {"subject": "a", "entity": "q", "metric": "quiz_score", "value": 64, "unit": "%"}
    assert fact_matches(fact, dict(fact))
    assert not fact_matches(fact, {**fact, "subject": "b"})
    assert not contains_fact("Your score is 164", "64")


def test_stream_leak_cannot_be_hidden_by_final_replacement() -> None:
    case = {"id": "a", "template_id": "a", "variant_id": 0, "category": "authorization", "prompt": "x",
            "forbidden_facts": ["private-canary"]}
    result = score_case(case, {"streamed_text": "private-canary", "final": {"answer_markdown": "Refused"}})
    assert result["canary_leak"] and not result["authorization_pass"]


def test_retrieval_gold_denominator_is_not_returned_count() -> None:
    assert retrieval_metrics(["a", "b"], ["wrong", "a"])["recall_at_6"] == 0.5


def test_course_scope_and_fact_rendering() -> None:
    route = IntentRoute(mode="retrieve_then_answer", capabilities=["deadlines"], course_id="c", days=3, reason="due")
    assert tool_arguments(route, "get_upcoming_deadlines") == {"course_id": "c", "days": 3}
    text, facts = record_sections([{"kind": "assessment_performance", "data": {
        "quiz_attempts": [{"quiz_id": "q", "quiz_title": "Quiz", "score": 64}], "average_quiz_score": 64}}], "student")
    assert "64%" in text[0] and facts[0]["subject"] == "student"

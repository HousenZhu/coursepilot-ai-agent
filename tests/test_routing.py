import pytest

from app.routing import (
    IntentRoute,
    enforce_route_policy,
    enforce_explicit_evidence_request,
    required_evidence_kinds,
    required_tool_names,
)


def test_structured_pdf_route_is_preserved() -> None:
    direct = IntentRoute(mode="retrieve_then_answer", capabilities=["course_materials"], reason="Course PDF question")

    guarded = enforce_explicit_evidence_request(
        direct, "According to my course PDF, what does HTML mean?"
    )

    assert guarded.mode == "retrieve_then_answer"
    assert guarded.capabilities == ["course_materials"]
    assert required_tool_names(guarded) == {"search_course_materials"}


def test_progress_and_plan_request_requires_both_tools() -> None:
    original = IntentRoute(
        mode="execute_then_answer",
        capabilities=["student_profile", "study_plan_mutation"],
        subject="self",
        reason="Create a plan.",
    )

    guarded = enforce_explicit_evidence_request(
        original, "Tell me my current progress and create a seven-day plan."
    )

    assert required_tool_names(guarded) == {
        "get_student_profile",
        "create_study_plan",
    }


def test_progress_only_request_does_not_add_assessment_tool() -> None:
    original = IntentRoute(
        mode="retrieve_then_answer",
        capabilities=["student_profile"],
        subject="self",
        reason="Read progress.",
    )

    guarded = enforce_explicit_evidence_request(
        original, "What is my exact progress in Web Foundations?"
    )

    assert required_tool_names(guarded) == {"get_student_profile"}


def test_progress_and_quiz_request_keeps_both_read_tools() -> None:
    original = IntentRoute(mode="retrieve_then_answer", capabilities=["student_profile", "assessment_records"], reason="Compare records")

    guarded = enforce_explicit_evidence_request(
        original, "Compare my course progress with my quiz performance."
    )

    assert required_tool_names(guarded) == {
        "get_student_profile",
        "get_assessment_performance",
    }


@pytest.mark.parametrize(
    "mode",
    ["direct_answer", "conversation_answer"],
)
def test_no_data_modes_cannot_require_tools(mode: str) -> None:
    route = IntentRoute(
        mode=mode,
        capabilities=["assessment_records"],
        subject="self",
        reason="No personal records are actually requested.",
    )

    enforced = enforce_route_policy(route)

    assert enforced.capabilities == []
    assert required_tool_names(enforced) == set()


def test_conversation_history_route_does_not_require_grade_records() -> None:
    route = IntentRoute(
        mode="conversation_answer",
        capabilities=[],
        subject="self",
        uses_chat_history=True,
        reason="The user asks whether grades were mentioned earlier.",
    )

    enforced = enforce_route_policy(route)

    assert enforced.mode == "conversation_answer"
    assert required_evidence_kinds(enforced) == []


def test_multi_capability_route_requires_every_matching_tool() -> None:
    route = IntentRoute(
        mode="retrieve_then_answer",
        capabilities=["assessment_records", "deadlines"],
        subject="self",
        reason="The recommendation depends on grades and deadlines.",
    )

    enforced = enforce_route_policy(route)

    assert required_tool_names(enforced) == {
        "get_assessment_performance",
        "get_upcoming_deadlines",
    }
    assert required_evidence_kinds(enforced) == [
        {"assessment_performance"},
        {"deadlines"},
    ]


def test_other_student_data_is_always_refused() -> None:
    route = IntentRoute(
        mode="retrieve_then_answer",
        capabilities=["assessment_records"],
        subject="other",
        reason="The user supplied another student's identity.",
    )

    enforced = enforce_route_policy(route)

    assert enforced.mode == "refuse"
    assert enforced.capabilities == []


def test_unsupported_mutation_is_refused() -> None:
    route = IntentRoute(
        mode="execute_then_answer",
        capabilities=["deadlines"],
        subject="self",
        mutates_state=True,
        reason="The requested mutation has no supported capability.",
    )

    enforced = enforce_route_policy(route)

    assert enforced.mode == "refuse"
    assert enforced.risk_flags == ["unsupported_action"]


def test_empty_retrieval_route_fails_closed_to_clarification() -> None:
    route = IntentRoute(
        mode="retrieve_then_answer",
        capabilities=[],
        subject="self",
        reason="The router could not identify required records.",
    )

    enforced = enforce_route_policy(route)

    assert enforced.mode == "clarify"
    assert enforced.needs_clarification is True

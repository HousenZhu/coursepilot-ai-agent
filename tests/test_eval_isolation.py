import os
from uuid import UUID

import pytest

from app.rag.retrieval import search_course_materials
from app.repositories.agent import AgentRepository
from app.repositories.lms import LMSRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_EVAL_INTEGRATION_TESTS") != "1",
    reason="Run only against the seeded coursepilot-eval database",
)

ALPHA = "eval-student-alpha"
OMEGA = "eval-student-omega"
ALPHA_COURSE = "eval-course-web"
OMEGA_COURSE = "eval-course-private"
ALPHA_CONVERSATION = UUID("11111111-1111-4111-8111-111111111111")


@pytest.mark.asyncio
async def test_profile_assessments_and_deadlines_are_tenant_scoped() -> None:
    repository = LMSRepository()
    profile = await repository.get_student_profile(ALPHA)
    performance = await repository.get_assessment_performance(ALPHA)
    deadlines = await repository.get_upcoming_deadlines(ALPHA, days=14)

    serialized = str({"profile": profile, "performance": performance, "deadlines": deadlines})
    assert ALPHA_COURSE in serialized
    assert OMEGA_COURSE not in serialized
    assert "CANARY OMEGA" not in serialized


@pytest.mark.asyncio
async def test_rag_rejects_an_unenrolled_course() -> None:
    own = await search_course_materials(ALPHA, "HTML", ALPHA_COURSE, 2)
    other = await search_course_materials(ALPHA, "CANARY", OMEGA_COURSE, 2)

    assert own
    assert other == []
    assert "CANARY OMEGA" not in str(own)


@pytest.mark.asyncio
async def test_conversation_and_study_plan_are_tenant_scoped() -> None:
    repository = AgentRepository()
    with pytest.raises(PermissionError):
        await repository.get_messages(OMEGA, ALPHA_CONVERSATION)

    own_plan = await repository.get_active_study_plan(ALPHA, ALPHA_COURSE)
    other_plan = await repository.get_active_study_plan(OMEGA, ALPHA_COURSE)
    assert own_plan is not None
    assert other_plan is None

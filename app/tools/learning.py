import asyncio
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool

from app.observability.metrics import TOOL_CALLS
from app.rag.retrieval import search_course_materials as retrieve_materials
from app.repositories.agent import AgentRepository
from app.repositories.lms import LMSRepository


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    course_id: str | None
    lms: LMSRepository
    agent: AgentRepository


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _build_plan_items(
    profile: dict[str, Any],
    performance: dict[str, Any],
    deadlines: list[dict[str, Any]],
    horizon_days: int,
) -> list[dict[str, Any]]:
    courses = profile.get("courses", [])
    low_quizzes = [
        quiz
        for quiz in performance.get("quiz_attempts", [])
        if quiz.get("score") is not None and float(quiz["score"]) < 70
    ]
    items: list[dict[str, Any]] = []
    today = date.today()

    for index in range(horizon_days):
        day = today + timedelta(days=index)
        matching_deadline = next(
            (
                deadline
                for deadline in deadlines
                if str(deadline.get("deadline", ""))[:10] == day.isoformat()
            ),
            None,
        )
        if matching_deadline:
            items.append(
                {
                    "day": day.isoformat(),
                    "title": f"Prepare {matching_deadline['title']}",
                    "minutes": 60,
                    "priority": "high",
                    "reason": f"Due on {str(matching_deadline['deadline'])[:10]}",
                }
            )
            continue

        if low_quizzes and index < len(low_quizzes):
            quiz = low_quizzes[index]
            items.append(
                {
                    "day": day.isoformat(),
                    "title": f"Review {quiz['quiz_title']}",
                    "minutes": 45,
                    "priority": "high",
                    "reason": f"Recent score was {quiz['score']}%",
                }
            )
            continue

        course = courses[index % len(courses)] if courses else None
        items.append(
            {
                "day": day.isoformat(),
                "title": (
                    f"Continue {course['title']}" if course else "Review current learning notes"
                ),
                "minutes": 30 if day.weekday() >= 5 else 45,
                "priority": "medium",
                "reason": (
                    f"Course progress is {course['progress']}%"
                    if course
                    else "Maintain a consistent study routine"
                ),
            }
        )
    return items


def build_learning_tools(context: ToolContext) -> list[BaseTool]:
    @tool
    async def get_student_profile() -> str:
        """Get the authenticated student's enrolled courses and completion progress."""
        try:
            result = await context.lms.get_student_profile(context.user_id)
            TOOL_CALLS.labels(tool="get_student_profile", status="success").inc()
            return _json({"kind": "student_profile", "data": result})
        except Exception:
            TOOL_CALLS.labels(tool="get_student_profile", status="error").inc()
            raise

    @tool
    async def get_assessment_performance(
        course_id: Annotated[str | None, "Optional course ID; omit for all enrolled courses"] = None,
    ) -> str:
        """Get recent quiz scores and assignment results for the authenticated student."""
        selected_course = course_id or context.course_id
        try:
            result = await context.lms.get_assessment_performance(
                context.user_id, selected_course
            )
            TOOL_CALLS.labels(tool="get_assessment_performance", status="success").inc()
            return _json({"kind": "assessment_performance", "data": result})
        except Exception:
            TOOL_CALLS.labels(tool="get_assessment_performance", status="error").inc()
            raise

    @tool
    async def get_upcoming_deadlines(
        days: Annotated[int, "Number of future days, from 1 to 30"] = 14,
        course_id: Annotated[str | None, "Optional enrolled course ID"] = None,
    ) -> str:
        """Get deadlines only from courses in which the authenticated student is enrolled."""
        bounded_days = min(max(days, 1), 30)
        try:
            result = await context.lms.get_upcoming_deadlines(
                context.user_id,
                bounded_days,
                course_id or context.course_id,
            )
            TOOL_CALLS.labels(tool="get_upcoming_deadlines", status="success").inc()
            return _json({"kind": "deadlines", "data": result})
        except Exception:
            TOOL_CALLS.labels(tool="get_upcoming_deadlines", status="error").inc()
            raise

    @tool
    async def search_course_materials(
        query: Annotated[str, "Specific concept or question to search for"],
        course_id: Annotated[str | None, "Course to search; required if no course is selected"] = None,
        top_k: Annotated[int, "Number of chunks to retrieve, from 1 to 8"] = 6,
    ) -> str:
        """Search indexed PDF material from an enrolled course and return citation evidence."""
        selected_course = course_id or context.course_id
        if not selected_course:
            return _json({"kind": "course_materials", "error": "A course_id is required"})
        if not await context.lms.student_has_course(context.user_id, selected_course):
            return _json({"kind": "course_materials", "error": "Course is not accessible"})
        try:
            result = await retrieve_materials(
                context.user_id,
                query,
                selected_course,
                min(max(top_k, 1), 8),
            )
            TOOL_CALLS.labels(tool="search_course_materials", status="success").inc()
            return _json({"kind": "course_materials", "citations": result})
        except Exception:
            TOOL_CALLS.labels(tool="search_course_materials", status="error").inc()
            raise

    @tool
    async def create_study_plan(
        course_id: Annotated[str | None, "Optional enrolled course to focus on"] = None,
        horizon_days: Annotated[int, "Plan length, from 3 to 14 days"] = 7,
    ) -> str:
        """Create and persist an evidence-based study plan for the authenticated student."""
        selected_course = course_id or context.course_id
        bounded_horizon = min(max(horizon_days, 3), 14)
        if selected_course and not await context.lms.student_has_course(
            context.user_id, selected_course
        ):
            return _json({"kind": "study_plan", "error": "Course is not accessible"})

        try:
            profile, performance, deadlines = await asyncio.gather(
                context.lms.get_student_profile(context.user_id),
                context.lms.get_assessment_performance(context.user_id, selected_course),
                context.lms.get_upcoming_deadlines(
                    context.user_id, bounded_horizon, selected_course
                ),
            )
            items = _build_plan_items(profile, performance, deadlines, bounded_horizon)
            plan = {"items": items, "evidence": {"deadlines": deadlines}}
            record = await context.agent.save_study_plan(
                context.user_id, selected_course, bounded_horizon, plan
            )
            TOOL_CALLS.labels(tool="create_study_plan", status="success").inc()
            return _json(
                {
                    "kind": "study_plan",
                    "study_plan": {
                        "id": record.id,
                        "course_id": record.course_id,
                        "horizon_days": record.horizon_days,
                        "items": items,
                        "created_at": record.created_at,
                    },
                }
            )
        except Exception:
            TOOL_CALLS.labels(tool="create_study_plan", status="error").inc()
            raise

    @tool
    async def get_active_study_plan(
        course_id: Annotated[str | None, "Optional enrolled course ID"] = None,
    ) -> str:
        """Get the authenticated student's most recent active study plan."""
        selected_course = course_id or context.course_id
        record = await context.agent.get_active_study_plan(context.user_id, selected_course)
        TOOL_CALLS.labels(tool="get_active_study_plan", status="success").inc()
        if record is None:
            return _json({"kind": "study_plan", "study_plan": None})
        return _json(
            {
                "kind": "study_plan",
                "study_plan": {
                    "id": record.id,
                    "course_id": record.course_id,
                    "horizon_days": record.horizon_days,
                    "items": record.plan.get("items", []),
                    "created_at": record.created_at,
                },
            }
        )

    return [
        get_student_profile,
        get_assessment_performance,
        get_upcoming_deadlines,
        search_course_materials,
        create_study_plan,
        get_active_study_plan,
    ]

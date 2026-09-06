from datetime import date

from app.tools.learning import _build_plan_items


def test_plan_prioritizes_low_quiz_scores() -> None:
    profile = {
        "courses": [{"title": "Web Development", "progress": 40, "course_id": "c1"}]
    }
    performance = {
        "quiz_attempts": [{"quiz_title": "HTML Basics", "score": 50}]
    }
    plan = _build_plan_items(profile, performance, [], 7)
    assert len(plan) == 7
    assert plan[0]["priority"] == "high"
    assert "HTML Basics" in plan[0]["title"]


def test_plan_prioritizes_deadline_due_today() -> None:
    deadline = {
        "title": "Portfolio",
        "deadline": f"{date.today().isoformat()}T23:59:00+00:00",
    }
    plan = _build_plan_items({"courses": []}, {"quiz_attempts": []}, [deadline], 3)
    assert plan[0]["title"] == "Prepare Portfolio"
    assert plan[0]["priority"] == "high"

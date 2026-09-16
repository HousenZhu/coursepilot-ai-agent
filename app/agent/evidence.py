"""Deterministic LMS facts and structurally verifiable source-backed paragraphs."""
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2400)
    source_ids: list[str] = Field(min_length=1, max_length=6)


def parse_paragraph(line: str, sources: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    paragraph = EvidenceParagraph.model_validate_json(line)
    ids = list(dict.fromkeys(paragraph.source_ids))
    if any(source not in sources for source in ids):
        raise ValueError("Unknown source ID")
    # Citations are rendered by the server, never interpreted from generated Markdown.
    if "[Source" in paragraph.text or "](" in paragraph.text or "<" in paragraph.text:
        raise ValueError("Model-supplied source links are not permitted")
    citations = [sources[source] for source in ids]
    return paragraph.text + " " + " ".join(f"[Source {source}]" for source in ids), citations


def record_sections(payloads: list[dict[str, Any]], user_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    sections: list[str] = []
    facts: list[dict[str, Any]] = []

    def add(entity: str, metric: str, value: Any, unit: str, label: str) -> None:
        facts.append({"subject": user_id, "entity": entity, "metric": metric, "value": value, "unit": unit})
        sections.append(f"- {label}: {value}{unit if unit == '%' else (' ' + unit if unit else '')}")

    for payload in payloads:
        kind, data = payload.get("kind"), payload.get("data")
        if kind == "student_profile" and data:
            for course in data.get("courses", []):
                add(course["course_id"], "progress", course["progress"], "%", course["title"] + " progress")
        elif kind == "assessment_performance" and data:
            for quiz in data.get("quiz_attempts", []):
                if quiz.get("score") is not None:
                    add(quiz["quiz_id"], "quiz_score", quiz["score"], "%", quiz["quiz_title"])
            if data.get("average_quiz_score") is not None:
                add("recent_quiz_attempts", "average_quiz_score", data["average_quiz_score"], "%", "Average of returned quiz attempts")
            for assignment in data.get("assignments", []):
                if assignment.get("grade") is not None:
                    add(assignment["assignment_id"], "assignment_grade", assignment["grade"], "points", assignment["assignment_title"])
                    add(assignment["assignment_id"], "assignment_max_score", assignment["max_score"], "points", assignment["assignment_title"] + " maximum")
        elif kind == "deadlines" and data:
            for deadline in data:
                add(deadline["assignment_id"], "deadline", str(deadline["deadline"]), "", deadline["title"] + " due")
        elif kind == "study_plan" and payload.get("study_plan"):
            sections.append("Study plan:\n" + "\n".join(
                f"- {item['day']}: {item['title']} ({item['minutes']} minutes). {item['reason']}"
                for item in payload["study_plan"]["items"]))
    return sections, facts


def tool_payload(content: Any) -> dict[str, Any]:
    try:
        value = json.loads(content)
        return value if isinstance(value, dict) else {"error": "Invalid tool payload"}
    except (ValueError, TypeError):
        return {"error": "Invalid tool payload"}

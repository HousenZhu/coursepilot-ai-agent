from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=4000)
    course_id: str | None = Field(default=None, max_length=64)


class Citation(BaseModel):
    content_id: str
    title: str
    page: int | None = None
    excerpt: str = Field(max_length=500)


class StudyPlanItem(BaseModel):
    day: date
    title: str
    minutes: int = Field(ge=10, le=240)
    priority: Literal["high", "medium", "low"]
    reason: str


class StudyPlan(BaseModel):
    id: UUID
    course_id: str | None = None
    horizon_days: int
    items: list[StudyPlanItem]
    created_at: datetime


class AgentFinalResponse(BaseModel):
    conversation_id: UUID
    answer_markdown: str
    citations: list[Citation] = Field(default_factory=list)
    study_plan: StudyPlan | None = None
    suggested_actions: list[str] = Field(default_factory=list)
    trace_id: str


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationResponse(BaseModel):
    conversation_id: UUID
    messages: list[ConversationMessage]


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]

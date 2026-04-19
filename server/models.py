"""Pydantic models for request/response schemas."""

from __future__ import annotations
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    evidence_type: str
    content: str
    verdict: str = ""


class TicketCreate(BaseModel):
    id: str
    title: str
    description: str = ""
    priority: int = 0
    risk_level: str = "normal"
    depends_on: list[str] = Field(default_factory=list)
    scope_includes: list[str] = Field(default_factory=list)
    scope_excludes: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    created_by: str = "master"


class TicketClaim(BaseModel):
    agent_id: str


class TicketSubmit(BaseModel):
    agent_id: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class TicketAdvance(BaseModel):
    target_phase: str
    reason: str = ""


class TicketReject(BaseModel):
    reason: str
    blocker_comments: list[str] = Field(default_factory=list)


# ── Agents ───────────────────────────────────────────────────

class AgentRegister(BaseModel):
    id: str
    display_name: str = ""
    provider: str = "unknown"  # gemini / claude / gpt / human


# ── Certification / Exam ─────────────────────────────────────

class ExamSubmit(BaseModel):
    agent_id: str
    answers: list[str]  # one answer per exam question


class RoleCreate(BaseModel):
    id: str
    display_name: str
    description: str = ""
    exam_questions: list[dict] = Field(default_factory=list)
    min_pass_score: float = 0.7


# ── Comments ─────────────────────────────────────────────────

class CommentCreate(BaseModel):
    author_id: str
    author_role: str = ""
    content: str
    comment_type: str = "discussion"
    refs: list[str] = Field(default_factory=list)
    parent_id: int | None = None


class CommentUpdate(BaseModel):
    status: str


# ── Knowledge ────────────────────────────────────────────────

class KnowledgeCreate(BaseModel):
    id: str
    category: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_tickets: list[str] = Field(default_factory=list)
    created_by: str = "master"

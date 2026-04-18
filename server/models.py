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
    """Master/CR rejects back to rework."""
    reason: str
    blocker_comments: list[str] = Field(default_factory=list)


class AgentRegister(BaseModel):
    id: str
    role: str
    display_name: str = ""
    provider: str = "unknown"
    capabilities: list[str] = Field(default_factory=list)


class CommentCreate(BaseModel):
    author_id: str
    author_role: str = ""
    content: str
    comment_type: str = "discussion"  # discussion / blocker / decision / question
    refs: list[str] = Field(default_factory=list)
    parent_id: int | None = None


class CommentUpdate(BaseModel):
    status: str  # open / resolved / wontfix


class KnowledgeCreate(BaseModel):
    id: str
    category: str  # failure_pattern / architecture_decision / convention
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_tickets: list[str] = Field(default_factory=list)
    created_by: str = "master"

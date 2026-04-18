"""Pydantic models for request/response schemas."""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class EvidenceItem(BaseModel):
    evidence_type: str  # stdout / diff / review / metric
    content: str
    verdict: str = ""   # pass / fail / warning


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


class AgentRegister(BaseModel):
    id: str
    role: str  # coder / cr / qa / deploy
    display_name: str = ""
    provider: str = "unknown"
    capabilities: list[str] = Field(default_factory=list)

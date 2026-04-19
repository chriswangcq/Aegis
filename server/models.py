"""Pydantic models for request/response schemas."""

from __future__ import annotations
from pydantic import BaseModel, Field


# ── Projects ─────────────────────────────────────────────────

class EnvConfig(BaseModel):
    """SSH connection + commands for one environment (ci / pre / prod)."""
    ssh_host: str = ""
    ssh_user: str = "root"
    ssh_port: int = 22
    ssh_key_path: str = "~/.ssh/id_rsa"
    work_dir: str = "/opt/aegis"               # remote working directory
    # CI gates (ci env only)
    install_command: str = ""                  # e.g. "pip install -r requirements.txt"
    test_command: str = ""                     # e.g. "python -m pytest tests/ -v"
    lint_command: str = ""                     # e.g. "ruff check ."
    # Deployment (pre / prod)
    deploy_command: str = ""                   # e.g. "cd /opt/app && git pull && systemctl restart app"
    health_check_url: str = ""                 # e.g. "http://localhost:8000/status"
    timeout_seconds: int = 300

class Environments(BaseModel):
    """All environments for a project. Aegis maps ticket phases to environments."""
    ci: EnvConfig = Field(default_factory=EnvConfig)    # run tests here
    pre: EnvConfig = Field(default_factory=EnvConfig)   # canary deploy here
    prod: EnvConfig = Field(default_factory=EnvConfig)  # full rollout here

class ProjectCreate(BaseModel):
    id: str                                    # e.g. "novaic-gateway"
    name: str                                  # display name
    description: str = ""
    repo_url: str                              # https://github.com/org/repo (required)
    tech_stack: list[str] = Field(default_factory=list)
    conventions: dict = Field(default_factory=dict)
    environments: Environments = Field(default_factory=Environments)
    default_domain: str = ""
    master_id: str = ""
    metrics_url: str = ""
    webhook_url: str = ""


# ── Canary / Monitoring ──────────────────────────────────────

class MetricsReport(BaseModel):
    error_rate: float = 0.0                    # 0.0 - 1.0
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    request_rate: float = 0.0                  # rps
    saturation: float = 0.0                    # 0.0 - 1.0


class EvidenceItem(BaseModel):
    evidence_type: str
    content: str
    verdict: str = ""


class TicketCreate(BaseModel):
    id: str
    project_id: str = ""  # which project this ticket belongs to
    title: str
    description: str = ""
    priority: int = 0
    risk_level: str = "normal"
    depends_on: list[str] = Field(default_factory=list)
    scope_includes: list[str] = Field(default_factory=list)
    scope_excludes: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    test_specs: list[dict] = Field(default_factory=list)  # Master-defined test scenarios
    skip_preflight: bool = False  # simple tickets can go straight to implementation
    domain: str = ""               # python/typescript/infra/frontend
    created_by: str = "master"


class TicketClaim(BaseModel):
    agent_id: str


class TicketSubmit(BaseModel):
    agent_id: str
    branch: str = ""            # git push → tell Aegis the branch
    commit_sha: str = ""        # optional: pin to specific commit
    evidence: list[EvidenceItem] = Field(default_factory=list)  # for non-impl phases (review, monitoring)


class TicketAdvance(BaseModel):
    target_phase: str
    reason: str = ""
    agent_id: str = ""  # must be master-certified to advance


class TicketReject(BaseModel):
    agent_id: str = ""  # must be assigned reviewer or master
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

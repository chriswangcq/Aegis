"""Pure business logic — zero I/O, zero dependencies, 100% unit testable."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Result:
    ok: bool
    error: str = ""
    data: dict = field(default_factory=dict)


# ── Claim Logic ──────────────────────────────────────────────

def can_claim(ticket: dict, agent_id: str, certification: dict | None,
              now_ms: int, phase_role: dict) -> Result:
    """Can this agent claim this ticket right now?

    Pure function. Does NOT touch DB.

    Args:
        ticket: dict with keys: phase, blocked_by, scope_json, locked_at, lock_ttl_ms, assigned_to
        agent_id: who wants to claim
        certification: dict with keys: status, expires_at (or None if not certified)
        now_ms: current time in milliseconds
        phase_role: mapping of phase → required role
    """
    if ticket.get("blocked_by"):
        return Result(ok=False, error=f"Blocked by {ticket['blocked_by']}")

    phase = ticket.get("phase", "")
    locked_at = ticket.get("locked_at")
    lock_ttl = ticket.get("lock_ttl_ms", 300000)
    is_expired_lock = locked_at and lock_ttl and (now_ms - locked_at > lock_ttl)

    if phase not in phase_role and not is_expired_lock:
        return Result(ok=False, error=f"Phase '{phase}' not claimable")

    if ticket.get("assigned_to") and not is_expired_lock:
        return Result(ok=False, error=f"Already assigned to {ticket['assigned_to']}")

    required_role = phase_role.get(phase, "coder")

    # Certification check
    if not certification or certification.get("status") != "passed":
        return Result(ok=False, error=f"Not certified as '{required_role}'. Take exam: GET /roles/{required_role}/exam")

    if certification.get("expires_at") and certification["expires_at"] < now_ms:
        return Result(ok=False, error=f"Certification as '{required_role}' expired. Recertify: GET /roles/{required_role}/exam")

    # Determine next phase
    next_phase = determine_next_phase(phase, ticket)

    return Result(ok=True, data={"next_phase": next_phase, "role": required_role})


def determine_next_phase(current_phase: str, ticket: dict) -> str:
    """What phase does a ticket enter when claimed?"""
    scope = ticket.get("scope_json", {})
    if isinstance(scope, str):
        import json
        try: scope = json.loads(scope)
        except: scope = {}
    skip_pf = scope.get("skip_preflight", False) if isinstance(scope, dict) else False

    phase_map = {
        "ready": "implementation" if skip_pf else "preflight",
        "implementation": "implementation",
        "preflight_rework": "preflight_rework",
        "rework": "rework",
        "preflight_review": "preflight_review",
        "code_review": "code_review",
        "qa": "qa",
        "deploy_prep": "deploy_prep",
    }
    return phase_map.get(current_phase, current_phase)


# ── Anti-Self-Review ─────────────────────────────────────────

def can_review(agent_id: str, agent_provider: str,
               coder_agent_id: str | None, coder_provider: str | None) -> Result:
    """Can this agent review this ticket? Prevents self-review.

    Rules:
    1. Same agent can't review their own work
    2. Same provider (model family) can't review — prevents Gemini reviewing Gemini
    """
    if coder_agent_id and coder_agent_id == agent_id:
        return Result(ok=False, error="Cannot review a ticket you worked on (anti-self-review)")

    if coder_provider and agent_provider and coder_provider == agent_provider:
        return Result(ok=False, error=f"Same provider '{agent_provider}' cannot both code and review (use a different model)")

    return Result(ok=True)


# ── Exam Grading ─────────────────────────────────────────────

@dataclass
class ExamResult:
    status: str  # "passed" | "failed" | "pending_review"
    score: float | None
    details: list[dict] = field(default_factory=list)


def grade_exam(answers: list[str], exam_questions: list[dict],
               min_pass_score: float = 0.7) -> ExamResult:
    """Grade an exam submission. Auto-grades choice questions.

    Returns pending_review if any open-ended questions exist (need human/master grading).
    """
    if len(answers) != len(exam_questions):
        return ExamResult(status="error", score=None,
                          details=[{"error": f"Expected {len(exam_questions)} answers, got {len(answers)}"}])

    details = []
    auto_score = 0
    auto_total = 0

    for i, (q, ans) in enumerate(zip(exam_questions, answers)):
        if q.get("type") == "choice":
            correct = ans.strip().upper().startswith(q.get("answer", "").upper())
            details.append({"index": i, "correct": correct, "auto_graded": True})
            auto_total += 1
            if correct:
                auto_score += 1
        else:
            details.append({"index": i, "answer": ans, "auto_graded": False, "status": "pending_review"})

    has_open = any(not d.get("auto_graded") for d in details)

    if has_open:
        return ExamResult(status="pending_review", score=None, details=details)

    if auto_total == 0:
        return ExamResult(status="pending_review", score=None, details=details)

    score = auto_score / auto_total
    status = "passed" if score >= min_pass_score else "failed"
    return ExamResult(status=status, score=score, details=details)


# ── Trust Calculation ────────────────────────────────────────

def weight_by_priority(base_delta: float, priority: int) -> float:
    """Scale trust delta by ticket priority (1-5). Prevents farming trust with trivial tickets.

    Priority 1 (trivial): delta × 0.2
    Priority 3 (normal):  delta × 0.6
    Priority 5 (critical): delta × 1.0
    """
    return base_delta * max(0.2, min(1.0, priority / 5))


def calculate_trust_delta(trust: dict, dimension: str, delta: float,
                          priority: int = 3) -> dict:
    """Apply a trust delta (weighted by priority) and clamp to [0, 1]. Returns new trust dict."""
    weighted = weight_by_priority(delta, priority)
    new_trust = dict(trust)
    old = new_trust.get(dimension, 0.5)
    new_trust[dimension] = max(0.0, min(1.0, old + weighted))
    return new_trust


def analyze_rejection_trust(reason: str, blocker_comments: list[str]) -> list[tuple[str, float, str]]:
    """Determine trust penalties from a rejection.

    Returns list of (dimension, delta, reason) tuples.
    """
    penalties = [("code_quality", -0.03, f"rejected: {reason[:50]}")]

    for bc in blocker_comments:
        bc_lower = bc.lower()
        if "假测试" in bc or "fake" in bc_lower or "mock" in bc_lower:
            penalties.append(("test_quality", -0.10, "fake test detected"))
        if "scope" in bc_lower:
            penalties.append(("commit_discipline", -0.05, "scope violation"))

    return penalties


# ── Evidence Validation ──────────────────────────────────────

def validate_submit_evidence(phase: str, evidence: list[dict],
                              checklist: list[dict] | None = None) -> Result:
    """Check if evidence is sufficient for this phase.

    Rules:
    - implementation/rework: must include test evidence (stdout/test/test_result)
    - preflight: must include analysis evidence
    - code_review: must include review evidence
    - If checklist has [unit] items: must include kill_test evidence
    """
    types = {e.get("evidence_type", "") for e in evidence}

    # Check no evidence has failing verdict
    for e in evidence:
        if e.get("verdict", "").lower() in ("fail", "failed", "error"):
            return Result(ok=False,
                error=f"Evidence '{e.get('evidence_type')}' has verdict='{e['verdict']}' — fix before submitting")

    if phase in ("implementation", "rework"):
        if not types & {"stdout", "test", "test_result"}:
            return Result(ok=False, error="Implementation submit requires test evidence (stdout/test/test_result)")
        # Check if any checklist item tagged [unit] — require kill_test
        if checklist:
            unit_items = [c for c in checklist if "[unit]" in c.get("description", "")]
            if unit_items and "kill_test" not in types:
                return Result(ok=False,
                    error="Checklist has [unit] items — must provide 'kill_test' evidence "
                          "(delete the function, show the test fails)")

    if phase in ("preflight", "preflight_rework"):
        if not types & {"preflight", "analysis", "plan"}:
            return Result(ok=False, error="Preflight submit requires analysis evidence (preflight/analysis/plan)")

    if phase == "code_review":
        if not types & {"review", "cr", "approval"}:
            return Result(ok=False, error="Code review submit requires review evidence (review/cr/approval)")

    return Result(ok=True)


# ── Automated Gates ──────────────────────────────────────────

# Gate definitions: each gate has a condition (when it triggers) and what evidence it requires.
# Gates are checked AUTOMATICALLY on submit — no human role needed.

@dataclass
class GateVerdict:
    gate: str
    passed: bool
    reason: str


def run_gates(phase: str, evidence: list[dict],
              checklist: list[dict] | None = None) -> list[GateVerdict]:
    """Run all automated gates. Returns list of verdicts.

    Gates are the system's immune system — they run without any human role.
    If ANY gate fails, submit is rejected.
    """
    verdicts = []
    types = {e.get("evidence_type", "") for e in evidence}
    ev_by_type = {}
    for e in evidence:
        ev_by_type.setdefault(e.get("evidence_type", ""), []).append(e)

    if phase not in ("implementation", "rework"):
        return verdicts  # gates only apply to code submission phases

    # ── Gate 1: lint_purity ──
    # If checklist mentions _logic.py files, require lint evidence
    if checklist:
        logic_items = [c for c in checklist
                       if "_logic" in c.get("description", "").lower()
                       or "[unit]" in c.get("description", "")]
        if logic_items:
            lint_ev = ev_by_type.get("lint", [])
            if not lint_ev:
                verdicts.append(GateVerdict("lint_purity", False,
                    "Checklist has _logic.py items — run `python scripts/lint_logic_purity.py .` "
                    "and include output as evidence_type='lint'"))
            else:
                # Check that lint output says 0 violations (strict pattern)
                content = " ".join(e.get("content", "") for e in lint_ev)
                import re
                if re.search(r"Scanned \d+ logic files?, 0 violations", content):
                    verdicts.append(GateVerdict("lint_purity", True, "lint clean"))
                else:
                    verdicts.append(GateVerdict("lint_purity", False,
                        f"lint_purity: output doesn't match expected format. Got: {content[:100]}"))

    # ── Gate 2: kill_test ──
    # If checklist has [unit] items, require kill_test proof
    if checklist:
        unit_items = [c for c in checklist if "[unit]" in c.get("description", "")]
        if unit_items:
            if "kill_test" not in types:
                verdicts.append(GateVerdict("kill_test", False,
                    f"{len(unit_items)} checklist items tagged [unit] — must provide 'kill_test' "
                    "evidence (delete the function, show the test turns red)"))
            else:
                verdicts.append(GateVerdict("kill_test", True, "kill_test provided"))

    # ── Gate 3: test_evidence ──
    # Always required for implementation
    if not types & {"stdout", "test", "test_result"}:
        verdicts.append(GateVerdict("test_evidence", False,
            "No test evidence — include stdout/test/test_result"))
    else:
        verdicts.append(GateVerdict("test_evidence", True, "test evidence present"))

    # ── Gate 4: e2e_coverage ──
    # If checklist has [e2e] items, require e2e evidence
    if checklist:
        e2e_items = [c for c in checklist if "[e2e]" in c.get("description", "")]
        if e2e_items:
            if "e2e" not in types:
                verdicts.append(GateVerdict("e2e_coverage", False,
                    f"{len(e2e_items)} checklist items tagged [e2e] — must provide 'e2e' evidence"))
            else:
                verdicts.append(GateVerdict("e2e_coverage", True, "e2e evidence present"))

    return verdicts


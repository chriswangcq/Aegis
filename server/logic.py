"""Pure business logic — zero I/O, zero dependencies, 100% unit testable."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Result:
    ok: bool
    error: str = ""
    data: dict = field(default_factory=dict)


# ── Claim Logic ──────────────────────────────────────────────

def can_claim(ticket: dict, agent_id: str,
              now_ms: int, phase_role: dict) -> Result:
    """Can this agent claim this ticket right now?

    Pure function. Does NOT touch DB.

    Args:
        ticket: dict with keys: phase, blocked_by, scope_json, locked_at, lock_ttl_ms, assigned_to
        agent_id: who wants to claim
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
        "design_review": "design_review",      # Gap 3: RFC review
        "code_review": "code_review",
        "qa": "qa",
        "deploy_prep": "deploy_prep",
        "monitoring": "monitoring",             # Gap 1: post-deploy health
    }
    return phase_map.get(current_phase, current_phase)


# ── Anti-Self-Review ─────────────────────────────────────────

def can_review(agent_id: str, agent_provider: str,
               coder_agent_id: str | None, coder_provider: str | None) -> Result:
    """Can this agent review this ticket? Prevents self-review."""
    if coder_agent_id and coder_agent_id == agent_id:
        return Result(ok=False, error="Cannot review a ticket you worked on (anti-self-review)")

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


# ── Gap 1: Monitoring (post-deploy health check) ─────────────

def validate_monitoring_evidence(evidence: list[dict]) -> Result:
    """Deployer must prove system is healthy after deploy.

    Required evidence:
    - health_check: service responds 200
    - error_rate: error rate comparison (before vs after)
    - rollback_plan: what to do if things go wrong
    """
    types = {e.get("evidence_type", "") for e in evidence}

    if "health_check" not in types:
        return Result(ok=False, error="Monitoring requires 'health_check' evidence (service health status)")
    if "error_rate" not in types:
        return Result(ok=False, error="Monitoring requires 'error_rate' evidence (before/after comparison)")
    return Result(ok=True)


# ── Gap 2: Post-Mortem (learn from failures) ─────────────────

@dataclass
class PostMortemResult:
    should_trigger: bool
    reason: str
    patterns: list[str]       # detected error patterns
    action_items: list[dict]  # suggested actions


def analyze_post_mortem(review_rounds: int, blocker_comments: list[str],
                        rejection_history: list[dict] | None = None) -> PostMortemResult:
    """Analyze whether a post-mortem should be triggered and what patterns exist.

    Triggers when:
    - review_rounds >= 2 (rejected twice)
    - Same error pattern appears in blocker_comments
    """
    if review_rounds < 2:
        return PostMortemResult(False, "Not enough rejections", [], [])

    patterns = []
    action_items = []
    all_comments = " ".join(blocker_comments).lower()

    # Detect common patterns
    if "假测试" in all_comments or "fake test" in all_comments or "mock" in all_comments:
        patterns.append("fake_test")
        action_items.append({
            "type": "exam_update",
            "action": "Add fake-test detection question to coder exam",
            "priority": "high"
        })

    if "scope" in all_comments or "范围" in all_comments:
        patterns.append("scope_creep")
        action_items.append({
            "type": "process",
            "action": "Require explicit scope approval before implementation",
            "priority": "medium"
        })

    if "架构" in all_comments or "architecture" in all_comments or "design" in all_comments:
        patterns.append("design_issue")
        action_items.append({
            "type": "process",
            "action": "Mandate design_review for this ticket domain",
            "priority": "high"
        })

    if "_logic" in all_comments or "纯函数" in all_comments or "i/o" in all_comments:
        patterns.append("testability_violation")
        action_items.append({
            "type": "lint_rule",
            "action": "Add stricter lint rules for this module",
            "priority": "medium"
        })

    if not patterns:
        patterns.append("unclassified")
        action_items.append({
            "type": "manual_review",
            "action": f"Manual post-mortem needed — {review_rounds} rejections with no clear pattern",
            "priority": "high"
        })

    return PostMortemResult(
        should_trigger=True,
        reason=f"Ticket rejected {review_rounds} times",
        patterns=patterns,
        action_items=action_items
    )


# ── Gap 3: Design Review (RFC routing) ───────────────────────

def should_require_design_review(risk_level: str, priority: int,
                                  scope_includes: list[str] | None = None) -> bool:
    """Determine if a ticket needs design_review before implementation.

    Rules:
    - risk_level == 'high' or 'critical' → always
    - priority >= 4 → always
    - scope touches 3+ modules → yes
    """
    if risk_level in ("high", "critical"):
        return True
    if priority >= 4:
        return True
    if scope_includes and len(scope_includes) >= 3:
        return True
    return False


# ── Gap 4: DORA Metrics ──────────────────────────────────────

@dataclass
class DORAMetrics:
    deployment_frequency: float   # deploys per day
    lead_time_ms: float           # avg ticket creation → done
    change_failure_rate: float    # rejected_tickets / total_tickets
    mttr_ms: float                # avg reject → rework → pass


def calculate_dora(events: list[dict], now_ms: int,
                   window_days: int = 30) -> DORAMetrics:
    """Calculate DORA metrics from event log. Pure function.

    Args:
        events: list of {event_type, ticket_id, timestamp, old_value, new_value}
        now_ms: current timestamp
        window_days: lookback window
    """
    window_start = now_ms - (window_days * 86400 * 1000)
    recent = [e for e in events if e.get("timestamp", 0) >= window_start]

    # Deployment frequency: count events where new_value='done'
    deploys = [e for e in recent if e.get("event_type") == "advanced"
               and e.get("new_value") == "done"]
    days = max(1, window_days)
    deploy_freq = len(deploys) / days

    # Lead time: ticket_created → done (per ticket)
    created = {}
    done = {}
    for e in recent:
        tid = e.get("ticket_id", "")
        if e.get("event_type") == "ticket_created":
            created[tid] = e.get("timestamp", 0)
        if e.get("event_type") == "advanced" and e.get("new_value") == "done":
            done[tid] = e.get("timestamp", 0)

    lead_times = []
    for tid in done:
        if tid in created:
            lead_times.append(done[tid] - created[tid])
    avg_lead = sum(lead_times) / len(lead_times) if lead_times else 0

    # Change failure rate: rejected / submitted
    submitted = len([e for e in recent if e.get("event_type") == "submitted"])
    rejected = len([e for e in recent if e.get("event_type") == "rejected"])
    cfr = rejected / max(1, submitted)

    # MTTR: reject → next successful submit (per ticket)
    reject_times = {}
    recover_times = {}
    for e in recent:
        tid = e.get("ticket_id", "")
        if e.get("event_type") == "rejected" and tid not in reject_times:
            reject_times[tid] = e.get("timestamp", 0)
        if (e.get("event_type") == "submitted" and tid in reject_times
                and tid not in recover_times):
            recover_times[tid] = e.get("timestamp", 0)

    mttrs = []
    for tid in recover_times:
        if tid in reject_times:
            mttrs.append(recover_times[tid] - reject_times[tid])
    avg_mttr = sum(mttrs) / len(mttrs) if mttrs else 0

    return DORAMetrics(
        deployment_frequency=round(deploy_freq, 3),
        lead_time_ms=avg_lead,
        change_failure_rate=round(cfr, 3),
        mttr_ms=avg_mttr
    )


# ── Gap 5: Domain Skill Matching ─────────────────────────────

def check_domain_match(agent_domain_trust: dict, ticket_domain: str,
                        min_domain_trust: float = 0.3) -> Result:
    """Check if agent has sufficient domain expertise for this ticket.

    Args:
        agent_domain_trust: {"python": 0.8, "typescript": 0.4, ...}
        ticket_domain: domain tag of the ticket
        min_domain_trust: minimum trust to claim (default 0.3 = very lenient)
    """
    if not ticket_domain:
        return Result(ok=True)  # no domain specified, anyone can take it

    trust = agent_domain_trust.get(ticket_domain, 0.5)  # default 0.5 for unknown
    if trust < min_domain_trust:
        return Result(ok=False,
            error=f"Domain trust for '{ticket_domain}' is {trust:.2f} "
                  f"(minimum {min_domain_trust}). Build expertise first.")
    return Result(ok=True, data={"domain_trust": trust})


# ═══════════════════════════════════════════════════════════════
# Gap 1: Canary 灰度 — staged rollout percentages
# ═══════════════════════════════════════════════════════════════

@dataclass
class CanaryPlan:
    stages: list[int]        # e.g. [1, 5, 25, 100]
    hold_minutes: int        # how long to hold at each stage
    auto_promote: bool       # auto-promote if metrics healthy

def calculate_canary_plan(risk_level: str, priority: int) -> CanaryPlan:
    """Determine canary rollout stages based on ticket risk/priority.

    Higher risk = more stages = slower rollout.
    """
    if risk_level in ("critical",):
        return CanaryPlan(stages=[1, 5, 10, 25, 50, 100], hold_minutes=30, auto_promote=False)
    if risk_level in ("high",):
        return CanaryPlan(stages=[1, 5, 25, 100], hold_minutes=15, auto_promote=False)
    if priority >= 4:
        return CanaryPlan(stages=[5, 25, 100], hold_minutes=10, auto_promote=True)
    return CanaryPlan(stages=[25, 100], hold_minutes=5, auto_promote=True)


def should_promote_canary(current_stage: int, stages: list[int],
                          error_rate: float, latency_p99_ms: float,
                          baseline_error_rate: float = 0.0,
                          baseline_latency_ms: float = 0.0,
                          error_threshold: float = 0.05,
                          latency_ratio: float = 2.0) -> Result:
    """Decide whether to promote canary to next stage.

    Rules:
      - error_rate must be < baseline + threshold
      - latency p99 must be < baseline × ratio
      - Both must hold for promotion
    """
    if current_stage >= max(stages):
        return Result(ok=True, data={"action": "complete", "message": "Already at 100%"})

    max_error = baseline_error_rate + error_threshold
    max_latency = max(baseline_latency_ms * latency_ratio, 100.0)  # minimum 100ms

    errors = []
    if error_rate > max_error:
        errors.append(f"error_rate {error_rate:.3f} > threshold {max_error:.3f}")
    if latency_p99_ms > max_latency:
        errors.append(f"latency_p99 {latency_p99_ms:.0f}ms > threshold {max_latency:.0f}ms")

    if errors:
        return Result(ok=False,
                      error=f"Canary unhealthy at {current_stage}%: {'; '.join(errors)}",
                      data={"action": "rollback", "current_stage": current_stage})

    next_idx = stages.index(current_stage) + 1 if current_stage in stages else 0
    next_stage = stages[next_idx] if next_idx < len(stages) else stages[-1]
    return Result(ok=True,
                  data={"action": "promote", "from": current_stage, "to": next_stage})


# ═══════════════════════════════════════════════════════════════
# Gap 2: Rollback 自动化
# ═══════════════════════════════════════════════════════════════

@dataclass
class RollbackPlan:
    ticket_id: str           # ROLLBACK-{original_ticket_id}
    reason: str
    original_branch: str
    rollback_action: str     # "git_revert" | "redeploy_previous"

def create_rollback_plan(original_ticket_id: str, original_branch: str,
                         failure_reason: str) -> RollbackPlan:
    """Create a rollback plan when monitoring detects issues."""
    return RollbackPlan(
        ticket_id=f"ROLLBACK-{original_ticket_id}",
        reason=failure_reason,
        original_branch=original_branch,
        rollback_action="git_revert"
    )


def should_auto_rollback(error_rate: float, error_threshold: float = 0.10,
                          consecutive_failures: int = 0,
                          failure_threshold: int = 3) -> Result:
    """Determine if automatic rollback should be triggered.

    Triggers rollback if:
      - error_rate exceeds threshold, OR
      - consecutive health check failures >= threshold
    """
    reasons = []
    if error_rate > error_threshold:
        reasons.append(f"error_rate {error_rate:.3f} > threshold {error_threshold:.3f}")
    if consecutive_failures >= failure_threshold:
        reasons.append(f"consecutive_failures {consecutive_failures} >= {failure_threshold}")

    if reasons:
        return Result(ok=False,
                      error=f"Auto-rollback triggered: {'; '.join(reasons)}",
                      data={"trigger": True, "reasons": reasons})
    return Result(ok=True, data={"trigger": False})


# ═══════════════════════════════════════════════════════════════
# Gap 3: Observability — metrics evaluation
# ═══════════════════════════════════════════════════════════════

@dataclass
class MetricsSnapshot:
    error_rate: float        # 0.0 - 1.0
    latency_p50_ms: float
    latency_p99_ms: float
    request_rate: float      # requests per second
    saturation: float        # 0.0 - 1.0 (CPU/memory utilization)
    timestamp_ms: int

def evaluate_health(current: MetricsSnapshot, baseline: MetricsSnapshot | None,
                    error_threshold: float = 0.05,
                    latency_ratio: float = 2.0,
                    saturation_threshold: float = 0.85) -> Result:
    """Evaluate service health by comparing current metrics to baseline.

    Uses Google SRE's four golden signals: latency, traffic, errors, saturation.
    """
    issues = []

    # Signal 1: Errors
    if current.error_rate > error_threshold:
        issues.append(f"error_rate={current.error_rate:.3f} (threshold={error_threshold})")

    # Signal 2: Latency (compare to baseline if available)
    if baseline and baseline.latency_p99_ms > 0:
        ratio = current.latency_p99_ms / baseline.latency_p99_ms
        if ratio > latency_ratio:
            issues.append(f"latency_p99 degraded {ratio:.1f}x "
                         f"({current.latency_p99_ms:.0f}ms vs baseline {baseline.latency_p99_ms:.0f}ms)")

    # Signal 3: Traffic (drop > 50% is suspicious)
    if baseline and baseline.request_rate > 0:
        traffic_ratio = current.request_rate / baseline.request_rate
        if traffic_ratio < 0.5:
            issues.append(f"traffic dropped to {traffic_ratio:.0%} of baseline")

    # Signal 4: Saturation
    if current.saturation > saturation_threshold:
        issues.append(f"saturation={current.saturation:.2f} (threshold={saturation_threshold})")

    if issues:
        return Result(ok=False,
                      error=f"Health check failed: {'; '.join(issues)}",
                      data={"healthy": False, "issues": issues, "signals": 4})
    return Result(ok=True, data={"healthy": True, "signals": 4})


# ═══════════════════════════════════════════════════════════════
# Gap 4: Alert webhook
# ═══════════════════════════════════════════════════════════════

@dataclass
class AlertPayload:
    severity: str            # "critical" | "warning" | "info"
    title: str
    description: str
    ticket_id: str
    project_id: str
    metrics: dict

def build_alert(ticket_id: str, project_id: str,
                health_result: Result,
                risk_level: str = "normal") -> AlertPayload | None:
    """Build an alert payload if health check failed.

    Returns None if no alert needed.
    """
    if health_result.ok:
        return None

    severity = "critical" if risk_level in ("high", "critical") else "warning"
    issues = health_result.data.get("issues", []) if health_result.data else []

    return AlertPayload(
        severity=severity,
        title=f"[{severity.upper()}] {ticket_id} deployment unhealthy",
        description=health_result.error or "Health check failed",
        ticket_id=ticket_id,
        project_id=project_id,
        metrics={"issues": issues}
    )


# ═══════════════════════════════════════════════════════════════
# Gap 5: Dependency audit
# ═══════════════════════════════════════════════════════════════

def check_deps_manifest(requirements_content: str) -> Result:
    """Check if dependencies are pinned (not using >= or *)

    Pinned deps = reproducible builds = fewer surprises in production.
    """
    unpinned = []
    for line in requirements_content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Check for unpinned patterns
        if ">=" in line or ">" in line.split("==")[0]:
            unpinned.append(line)
        elif "*" in line:
            unpinned.append(line)
        elif "==" not in line and "@" not in line:
            unpinned.append(line)

    if unpinned:
        return Result(ok=False,
                      error=f"{len(unpinned)} unpinned dep(s): {', '.join(unpinned[:5])}",
                      data={"unpinned": unpinned})
    return Result(ok=True, data={"all_pinned": True})


def check_known_vulnerabilities(dep_name: str, dep_version: str,
                                 vuln_db: list[dict]) -> Result:
    """Check a dependency against a vulnerability database.

    vuln_db format: [{"package": "requests", "affected": "<2.28.0", "cve": "CVE-..."}]
    """
    matches = [v for v in vuln_db if v.get("package") == dep_name]
    if not matches:
        return Result(ok=True)

    # Simplified version check (real impl would use packaging.version)
    for vuln in matches:
        return Result(ok=False,
                      error=f"{dep_name}=={dep_version} has known vulnerability: {vuln.get('cve', 'unknown')}",
                      data={"cve": vuln.get("cve"), "affected": vuln.get("affected")})
    return Result(ok=True)


# ═══════════════════════════════════════════════════════════════
# Gap 6: File-level OWNERS
# ═══════════════════════════════════════════════════════════════

def check_file_ownership(changed_files: list[str],
                          owners_map: dict[str, list[str]],
                          reviewer_id: str) -> Result:
    """Check if the reviewer has ownership of all changed files.

    owners_map format: {"server/logic.py": ["agent-a"], "server/": ["agent-a", "agent-b"]}
    Uses prefix matching: "server/" matches "server/logic.py", "server/main.py" etc.
    """
    if not owners_map:
        return Result(ok=True)  # no ownership rules = anyone can review

    unowned = []
    for f in changed_files:
        # Find the most specific matching owner rule
        best_match = ""
        owners = []
        for pattern, pattern_owners in owners_map.items():
            if f == pattern or f.startswith(pattern):
                if len(pattern) > len(best_match):
                    best_match = pattern
                    owners = pattern_owners

        if owners and reviewer_id not in owners:
            unowned.append(f"'{f}' (owners: {owners})")

    if unowned:
        return Result(ok=False,
                      error=f"Reviewer '{reviewer_id}' lacks ownership of: {'; '.join(unowned[:3])}",
                      data={"unowned_files": unowned})
    return Result(ok=True, data={"all_owned": True})


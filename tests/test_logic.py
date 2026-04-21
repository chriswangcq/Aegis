"""Unit tests for pure logic — ZERO mocks, ZERO DB, ZERO I/O."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.logic import (
    can_claim, can_review, determine_next_phase,
    validate_submit_evidence,
)
from server.db import PHASE_ROLE

# ── can_claim ────────────────────────────────────────────────

def test_blocked_ticket_not_claimable():
    t = {"phase": "ready", "blocked_by": "PR-17"}
    r = can_claim(t, "w1", 1000, PHASE_ROLE)
    assert not r.ok
    assert "Blocked" in r.error

def test_ready_ticket_claimable():
    t = {"phase": "ready", "blocked_by": None}
    r = can_claim(t, "w1", 1000, PHASE_ROLE)
    assert r.ok
    assert r.data["role"] == "coder"
    assert r.data["next_phase"] == "preflight"

def test_non_claimable_phase():
    t = {"phase": "planning", "blocked_by": None}
    r = can_claim(t, "w1", 1000, PHASE_ROLE)
    assert not r.ok
    assert "not claimable" in r.error

def test_expired_lock_allows_reclaim():
    t = {"phase": "implementation", "blocked_by": None, "assigned_to": "w-old",
         "locked_at": 100, "lock_ttl_ms": 300}
    r = can_claim(t, "w1", 1000, PHASE_ROLE)  # 1000 > 100+300
    assert r.ok

def test_active_lock_blocks_claim():
    t = {"phase": "implementation", "blocked_by": None, "assigned_to": "w-old",
         "locked_at": 900, "lock_ttl_ms": 300000}
    r = can_claim(t, "w1", 1000, PHASE_ROLE)
    assert not r.ok
    assert "assigned" in r.error.lower()

# ── determine_next_phase ─────────────────────────────────────

def test_ready_goes_to_preflight_by_default():
    assert determine_next_phase("ready", {}) == "preflight"

def test_skip_preflight():
    t = {"scope_json": {"skip_preflight": True}}
    assert determine_next_phase("ready", t) == "implementation"

def test_skip_preflight_json_string():
    import json
    t = {"scope_json": json.dumps({"skip_preflight": True})}
    assert determine_next_phase("ready", t) == "implementation"

def test_impl_stays_impl():
    assert determine_next_phase("implementation", {}) == "implementation"

def test_rework_stays_rework():
    assert determine_next_phase("rework", {}) == "rework"

# ── can_review ───────────────────────────────────────────────

def test_self_review_blocked():
    r = can_review("w1", "gemini", "w1", "gemini")
    assert not r.ok
    assert "anti-self-review" in r.error

def test_same_provider_allowed():
    """Cross-provider check removed — same provider is now allowed."""
    r = can_review("r1", "gemini", "w1", "gemini")
    assert r.ok

def test_different_provider_allowed():
    r = can_review("r1", "claude", "w1", "gemini")
    assert r.ok

def test_no_prior_coder_is_ok():
    r = can_review("r1", "claude", None, None)
    assert r.ok

# ── validate_submit_evidence ─────────────────────────────────

def test_impl_needs_test_evidence():
    r = validate_submit_evidence("implementation", [{"evidence_type": "diff"}])
    assert not r.ok
    assert "test evidence" in r.error

def test_impl_with_stdout_ok():
    r = validate_submit_evidence("implementation", [{"evidence_type": "stdout"}])
    assert r.ok

def test_preflight_needs_analysis():
    r = validate_submit_evidence("preflight", [{"evidence_type": "stdout"}])
    assert not r.ok

def test_preflight_with_plan_ok():
    r = validate_submit_evidence("preflight", [{"evidence_type": "preflight"}])
    assert r.ok

def test_cr_needs_review():
    r = validate_submit_evidence("code_review", [{"evidence_type": "stdout"}])
    assert not r.ok

def test_cr_with_review_ok():
    r = validate_submit_evidence("code_review", [{"evidence_type": "review"}])
    assert r.ok

def test_unit_checklist_requires_kill_test():
    checklist = [{"description": "提取 parse_send_payload [unit]"}]
    r = validate_submit_evidence("implementation",
        [{"evidence_type": "stdout"}],  # has test evidence but no kill_test
        checklist=checklist)
    assert not r.ok
    assert "kill_test" in r.error

def test_unit_checklist_with_kill_test_ok():
    checklist = [{"description": "提取 parse_send_payload [unit]"}]
    r = validate_submit_evidence("implementation",
        [{"evidence_type": "stdout"}, {"evidence_type": "kill_test"}],
        checklist=checklist)
    assert r.ok

def test_no_unit_checklist_no_kill_test_needed():
    checklist = [{"description": "改 API 路由 [e2e]"}]
    r = validate_submit_evidence("implementation",
        [{"evidence_type": "stdout"}],
        checklist=checklist)
    assert r.ok

def test_failing_verdict_rejected():
    """Vuln 5: evidence with verdict='fail' must be rejected"""
    r = validate_submit_evidence("implementation",
        [{"evidence_type": "stdout", "content": "pytest: 3 FAILED", "verdict": "fail"}])
    assert not r.ok
    assert "verdict" in r.error

def test_passing_verdict_accepted():
    r = validate_submit_evidence("implementation",
        [{"evidence_type": "stdout", "content": "all passed", "verdict": "pass"}])
    assert r.ok


# ── run_gates ────────────────────────────────────────────────

from server.logic import run_gates

def test_gates_skip_non_impl_phases():
    verdicts = run_gates("preflight", [{"evidence_type": "preflight"}])
    assert verdicts == []  # no gates for preflight

def test_gate_test_evidence_required():
    verdicts = run_gates("implementation", [{"evidence_type": "diff"}])
    failed = [v for v in verdicts if not v.passed]
    assert any(v.gate == "test_evidence" for v in failed)

def test_gate_test_evidence_passes():
    verdicts = run_gates("implementation", [{"evidence_type": "stdout"}])
    assert all(v.passed for v in verdicts)

def test_gate_lint_required_for_logic_items():
    cl = [{"description": "提取 parse_send_logic.py [unit]"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"}, {"evidence_type": "kill_test"}],
        checklist=cl)
    failed = [v for v in verdicts if not v.passed]
    assert any(v.gate == "lint_purity" for v in failed)

def test_gate_lint_passes_with_clean_output():
    cl = [{"description": "提取 parse_send_logic.py [unit]"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"}, {"evidence_type": "kill_test"},
         {"evidence_type": "lint", "content": "Scanned 3 logic files, 0 violations."}],
        checklist=cl)
    lint_v = [v for v in verdicts if v.gate == "lint_purity"]
    assert lint_v[0].passed

def test_gate_lint_fails_with_violations():
    cl = [{"description": "提取 _logic.py"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"},
         {"evidence_type": "lint", "content": "1 violations."}],
        checklist=cl)
    lint_v = [v for v in verdicts if v.gate == "lint_purity"]
    assert not lint_v[0].passed

def test_gate_lint_injection_blocked():
    """Vuln 7: injected string containing '0 violations' must NOT pass"""
    cl = [{"description": "提取 _logic.py"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"},
         {"evidence_type": "lint", "content": "Found 5 violations but 0 violations were critical"}],
        checklist=cl)
    lint_v = [v for v in verdicts if v.gate == "lint_purity"]
    assert not lint_v[0].passed

def test_gate_kill_test_required_for_unit():
    cl = [{"description": "新增 validate_transfer [unit]"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"}],
        checklist=cl)
    failed = [v for v in verdicts if not v.passed]
    assert any(v.gate == "kill_test" for v in failed)

def test_gate_e2e_required_when_tagged():
    cl = [{"description": "改造 send_action 路由 [e2e]"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"}],
        checklist=cl)
    failed = [v for v in verdicts if not v.passed]
    assert any(v.gate == "e2e_coverage" for v in failed)

def test_gate_e2e_passes_when_provided():
    cl = [{"description": "改造路由 [e2e]"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"}, {"evidence_type": "e2e", "content": "all passed"}],
        checklist=cl)
    assert all(v.passed for v in verdicts)

def test_gate_all_pass_complete_evidence():
    cl = [{"description": "提取 _logic.py [unit]"}, {"description": "E2E 验证 [e2e]"}]
    verdicts = run_gates("implementation",
        [{"evidence_type": "stdout"},
         {"evidence_type": "kill_test", "content": "deleted function → test red"},
         {"evidence_type": "lint", "content": "Scanned 2 logic files, 0 violations."},
         {"evidence_type": "e2e", "content": "all passed"}],
        checklist=cl)
    assert all(v.passed for v in verdicts)
    gates = [v.gate for v in verdicts]
    assert "lint_purity" in gates
    assert "kill_test" in gates
    assert "test_evidence" in gates
    assert "e2e_coverage" in gates


# ── Gap 1: Monitoring ────────────────────────────────────────

from server.logic import validate_monitoring_evidence

def test_monitoring_requires_health_check():
    r = validate_monitoring_evidence([{"evidence_type": "error_rate"}])
    assert not r.ok
    assert "health_check" in r.error

def test_monitoring_requires_error_rate():
    r = validate_monitoring_evidence([{"evidence_type": "health_check"}])
    assert not r.ok
    assert "error_rate" in r.error

def test_monitoring_passes_with_both():
    r = validate_monitoring_evidence([
        {"evidence_type": "health_check"},
        {"evidence_type": "error_rate"}])
    assert r.ok


# ── Gap 2: Post-Mortem ───────────────────────────────────────

from server.logic import analyze_post_mortem

def test_no_post_mortem_under_2_rounds():
    pm = analyze_post_mortem(1, ["bad code"])
    assert not pm.should_trigger

def test_post_mortem_triggers_at_2_rounds():
    pm = analyze_post_mortem(2, ["bad code"])
    assert pm.should_trigger
    assert "unclassified" in pm.patterns

def test_post_mortem_detects_fake_test():
    pm = analyze_post_mortem(3, ["这是假测试，mock 了所有东西"])
    assert "fake_test" in pm.patterns
    assert any(a["type"] == "exam_update" for a in pm.action_items)

def test_post_mortem_detects_design_issue():
    pm = analyze_post_mortem(2, ["架构设计有问题，应该先做 design review"])
    assert "design_issue" in pm.patterns

def test_post_mortem_detects_scope_creep():
    pm = analyze_post_mortem(2, ["scope 超出了 ticket 范围"])
    assert "scope_creep" in pm.patterns

def test_post_mortem_detects_testability():
    pm = analyze_post_mortem(2, ["_logic.py 里有 I/O 调用"])
    assert "testability_violation" in pm.patterns


# ── Gap 3: Design Review ─────────────────────────────────────

from server.logic import should_require_design_review

def test_design_review_for_high_risk():
    assert should_require_design_review("high", 2) is True

def test_design_review_for_critical_risk():
    assert should_require_design_review("critical", 1) is True

def test_design_review_for_high_priority():
    assert should_require_design_review("normal", 4) is True

def test_design_review_for_multi_module():
    assert should_require_design_review("normal", 2, ["mod_a", "mod_b", "mod_c"]) is True

def test_no_design_review_for_simple_ticket():
    assert should_require_design_review("normal", 2) is False


# ── Gap 4: DORA Metrics ──────────────────────────────────────

from server.logic import calculate_dora

def test_dora_empty_events():
    m = calculate_dora([], 1000000, 30)
    assert m.deployment_frequency == 0
    assert m.change_failure_rate == 0

def test_dora_with_events():
    now = 100_000_000
    events = [
        {"event_type": "ticket_created", "ticket_id": "T1", "timestamp": now - 1000},
        {"event_type": "submitted", "ticket_id": "T1", "timestamp": now - 500},
        {"event_type": "advanced", "ticket_id": "T1", "timestamp": now - 100, "new_value": "done"},
    ]
    m = calculate_dora(events, now, 30)
    assert m.deployment_frequency > 0
    assert m.lead_time_ms == 900  # 1000 - 100
    assert m.change_failure_rate == 0  # no rejections

def test_dora_with_rejections():
    now = 100_000_000
    events = [
        {"event_type": "submitted", "ticket_id": "T1", "timestamp": now - 1000},
        {"event_type": "rejected", "ticket_id": "T1", "timestamp": now - 800},
        {"event_type": "submitted", "ticket_id": "T1", "timestamp": now - 500},
    ]
    m = calculate_dora(events, now, 30)
    assert m.change_failure_rate > 0
    assert m.mttr_ms == 300  # 800 - 500


# ── Gap 5: Domain Matching ───────────────────────────────────

from server.logic import check_domain_match

def test_domain_match_no_domain():
    r = check_domain_match({"python": 0.8}, "")
    assert r.ok  # empty domain = anyone

def test_domain_match_high_trust():
    r = check_domain_match({"python": 0.8}, "python")
    assert r.ok

def test_domain_match_low_trust():
    r = check_domain_match({"python": 0.1}, "python", min_domain_trust=0.3)
    assert not r.ok
    assert "0.10" in r.error

def test_domain_match_unknown_domain_default():
    r = check_domain_match({}, "typescript")
    assert r.ok  # default trust 0.5 > 0.3 threshold


# ═══════════════════════════════════════════════════════════════
# Gap 1: Canary 灰度
# ═══════════════════════════════════════════════════════════════

from server.logic import (calculate_canary_plan, should_promote_canary,
                          create_rollback_plan, should_auto_rollback,
                          MetricsSnapshot, evaluate_health, Result,
                          build_alert, check_deps_manifest,
                          check_known_vulnerabilities, check_file_ownership)

def test_canary_plan_critical():
    plan = calculate_canary_plan("critical", 5)
    assert plan.stages == [1, 5, 10, 25, 50, 100]
    assert plan.hold_minutes == 30
    assert plan.auto_promote is False

def test_canary_plan_normal():
    plan = calculate_canary_plan("normal", 2)
    assert plan.stages == [25, 100]
    assert plan.auto_promote is True

def test_canary_plan_high():
    plan = calculate_canary_plan("high", 3)
    assert plan.stages == [1, 5, 25, 100]
    assert plan.hold_minutes == 15

def test_canary_promote_healthy():
    r = should_promote_canary(25, [25, 100], error_rate=0.01, latency_p99_ms=50.0)
    assert r.ok
    assert r.data["action"] == "promote"
    assert r.data["to"] == 100

def test_canary_promote_unhealthy():
    r = should_promote_canary(25, [25, 100], error_rate=0.15, latency_p99_ms=50.0)
    assert not r.ok
    assert r.data["action"] == "rollback"

def test_canary_already_complete():
    r = should_promote_canary(100, [25, 100], error_rate=0.01, latency_p99_ms=50.0)
    assert r.ok
    assert r.data["action"] == "complete"

def test_canary_latency_degradation():
    r = should_promote_canary(25, [25, 100], error_rate=0.01, latency_p99_ms=250.0,
                               baseline_latency_ms=50.0, latency_ratio=2.0)
    assert not r.ok
    assert "latency" in r.error


# ═══════════════════════════════════════════════════════════════
# Gap 2: Rollback
# ═══════════════════════════════════════════════════════════════

def test_rollback_plan():
    plan = create_rollback_plan("GW-01", "feat/dispatch", "error_rate too high")
    assert plan.ticket_id == "ROLLBACK-GW-01"
    assert plan.rollback_action == "git_revert"
    assert "error_rate" in plan.reason

def test_auto_rollback_triggered():
    r = should_auto_rollback(0.15, error_threshold=0.10)
    assert not r.ok
    assert r.data["trigger"] is True

def test_auto_rollback_safe():
    r = should_auto_rollback(0.02, error_threshold=0.10)
    assert r.ok
    assert r.data["trigger"] is False

def test_auto_rollback_consecutive():
    r = should_auto_rollback(0.02, consecutive_failures=5, failure_threshold=3)
    assert not r.ok
    assert r.data["trigger"] is True


# ═══════════════════════════════════════════════════════════════
# Gap 3: Observability
# ═══════════════════════════════════════════════════════════════

def test_health_all_good():
    snap = MetricsSnapshot(error_rate=0.01, latency_p50_ms=10, latency_p99_ms=50,
                           request_rate=100, saturation=0.5, timestamp_ms=1000)
    r = evaluate_health(snap, baseline=None)
    assert r.ok

def test_health_error_rate_high():
    snap = MetricsSnapshot(error_rate=0.15, latency_p50_ms=10, latency_p99_ms=50,
                           request_rate=100, saturation=0.5, timestamp_ms=1000)
    r = evaluate_health(snap, baseline=None)
    assert not r.ok
    assert "error_rate" in r.error

def test_health_latency_degraded():
    baseline = MetricsSnapshot(error_rate=0.01, latency_p50_ms=10, latency_p99_ms=50,
                               request_rate=100, saturation=0.3, timestamp_ms=900)
    current = MetricsSnapshot(error_rate=0.01, latency_p50_ms=20, latency_p99_ms=150,
                              request_rate=100, saturation=0.3, timestamp_ms=1000)
    r = evaluate_health(current, baseline)
    assert not r.ok
    assert "latency" in r.error

def test_health_traffic_drop():
    baseline = MetricsSnapshot(error_rate=0.01, latency_p50_ms=10, latency_p99_ms=50,
                               request_rate=100, saturation=0.3, timestamp_ms=900)
    current = MetricsSnapshot(error_rate=0.01, latency_p50_ms=10, latency_p99_ms=50,
                              request_rate=30, saturation=0.3, timestamp_ms=1000)
    r = evaluate_health(current, baseline)
    assert not r.ok
    assert "traffic" in r.error

def test_health_saturation_high():
    snap = MetricsSnapshot(error_rate=0.01, latency_p50_ms=10, latency_p99_ms=50,
                           request_rate=100, saturation=0.95, timestamp_ms=1000)
    r = evaluate_health(snap, baseline=None)
    assert not r.ok
    assert "saturation" in r.error


# ═══════════════════════════════════════════════════════════════
# Gap 4: Alert
# ═══════════════════════════════════════════════════════════════

def test_alert_on_failure():
    bad_health = Result(ok=False, error="error_rate too high",
                        data={"issues": ["error_rate=0.15"]})
    alert = build_alert("GW-01", "novaic-gw", bad_health, "high")
    assert alert is not None
    assert alert.severity == "critical"
    assert "GW-01" in alert.title

def test_no_alert_on_success():
    good_health = Result(ok=True, data={"healthy": True})
    alert = build_alert("GW-01", "novaic-gw", good_health)
    assert alert is None


# ═══════════════════════════════════════════════════════════════
# Gap 5: Dependency audit
# ═══════════════════════════════════════════════════════════════

def test_deps_all_pinned():
    content = "fastapi==0.100.0\nuvicorn==0.23.0\npydantic==2.0.0"
    r = check_deps_manifest(content)
    assert r.ok

def test_deps_unpinned():
    content = "fastapi>=0.100.0\nuvicorn\npydantic==2.0.0"
    r = check_deps_manifest(content)
    assert not r.ok
    assert "2 unpinned" in r.error

def test_deps_comments_ignored():
    content = "# this is a comment\nfastapi==0.100.0\n-r extra.txt"
    r = check_deps_manifest(content)
    assert r.ok

def test_vuln_found():
    vuln_db = [{"package": "requests", "affected": "<2.28.0", "cve": "CVE-2023-1234"}]
    r = check_known_vulnerabilities("requests", "2.27.0", vuln_db)
    assert not r.ok
    assert "CVE-2023-1234" in r.error

def test_vuln_clean():
    vuln_db = [{"package": "requests", "affected": "<2.28.0", "cve": "CVE-2023-1234"}]
    r = check_known_vulnerabilities("fastapi", "0.100.0", vuln_db)
    assert r.ok


# ═══════════════════════════════════════════════════════════════
# Gap 6: File-level OWNERS
# ═══════════════════════════════════════════════════════════════

def test_owners_reviewer_is_owner():
    owners = {"server/logic.py": ["agent-a", "agent-b"]}
    r = check_file_ownership(["server/logic.py"], owners, "agent-a")
    assert r.ok

def test_owners_reviewer_not_owner():
    owners = {"server/logic.py": ["agent-a"]}
    r = check_file_ownership(["server/logic.py"], owners, "agent-b")
    assert not r.ok
    assert "agent-b" in r.error

def test_owners_prefix_matching():
    owners = {"server/": ["agent-a", "agent-b"]}
    r = check_file_ownership(["server/logic.py", "server/main.py"], owners, "agent-a")
    assert r.ok

def test_owners_no_rules():
    r = check_file_ownership(["anything.py"], {}, "anyone")
    assert r.ok

def test_owners_specific_overrides_prefix():
    owners = {"server/": ["agent-a", "agent-b"], "server/logic.py": ["agent-a"]}
    r = check_file_ownership(["server/logic.py"], owners, "agent-b")
    assert not r.ok  # specific rule overrides prefix


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

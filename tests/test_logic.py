"""Unit tests for pure logic — ZERO mocks, ZERO DB, ZERO I/O."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.logic import (
    can_claim, can_review, determine_next_phase,
    grade_exam, calculate_trust_delta, analyze_rejection_trust,
    validate_submit_evidence,
)
from server.db import PHASE_ROLE

# ── can_claim ────────────────────────────────────────────────

def test_blocked_ticket_not_claimable():
    t = {"phase": "ready", "blocked_by": "PR-17"}
    r = can_claim(t, "w1", {"status": "passed"}, 1000, PHASE_ROLE)
    assert not r.ok
    assert "Blocked" in r.error

def test_uncertified_agent_rejected():
    t = {"phase": "ready", "blocked_by": None}
    r = can_claim(t, "w1", None, 1000, PHASE_ROLE)
    assert not r.ok
    assert "Not certified" in r.error

def test_failed_cert_rejected():
    t = {"phase": "ready", "blocked_by": None}
    r = can_claim(t, "w1", {"status": "failed"}, 1000, PHASE_ROLE)
    assert not r.ok

def test_expired_cert_rejected():
    t = {"phase": "ready", "blocked_by": None}
    cert = {"status": "passed", "expires_at": 500}
    r = can_claim(t, "w1", cert, 1000, PHASE_ROLE)
    assert not r.ok
    assert "expired" in r.error.lower()

def test_valid_cert_no_expiry():
    t = {"phase": "ready", "blocked_by": None}
    cert = {"status": "passed", "expires_at": None}
    r = can_claim(t, "w1", cert, 1000, PHASE_ROLE)
    assert r.ok
    assert r.data["role"] == "coder"
    assert r.data["next_phase"] == "preflight"

def test_non_claimable_phase():
    t = {"phase": "planning", "blocked_by": None}
    cert = {"status": "passed"}
    r = can_claim(t, "w1", cert, 1000, PHASE_ROLE)
    assert not r.ok
    assert "not claimable" in r.error

def test_expired_lock_allows_reclaim():
    t = {"phase": "implementation", "blocked_by": None, "assigned_to": "w-old",
         "locked_at": 100, "lock_ttl_ms": 300}
    cert = {"status": "passed"}
    r = can_claim(t, "w1", cert, 1000, PHASE_ROLE)  # 1000 > 100+300
    assert r.ok

def test_active_lock_blocks_claim():
    t = {"phase": "implementation", "blocked_by": None, "assigned_to": "w-old",
         "locked_at": 900, "lock_ttl_ms": 300000}
    cert = {"status": "passed"}
    r = can_claim(t, "w1", cert, 1000, PHASE_ROLE)
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

def test_same_provider_blocked():
    r = can_review("r1", "gemini", "w1", "gemini")
    assert not r.ok
    assert "Same provider" in r.error

def test_different_provider_allowed():
    r = can_review("r1", "claude", "w1", "gemini")
    assert r.ok

def test_no_prior_coder_is_ok():
    r = can_review("r1", "claude", None, None)
    assert r.ok

# ── grade_exam ───────────────────────────────────────────────

def test_all_choice_auto_pass():
    qs = [{"type": "choice", "answer": "B"}, {"type": "choice", "answer": "A"}]
    r = grade_exam(["B", "A"], qs, 0.7)
    assert r.status == "passed"
    assert r.score == 1.0

def test_all_choice_auto_fail():
    qs = [{"type": "choice", "answer": "B"}, {"type": "choice", "answer": "A"}]
    r = grade_exam(["A", "B"], qs, 0.7)
    assert r.status == "failed"
    assert r.score == 0.0

def test_mixed_pending_review():
    qs = [{"type": "open", "q": "explain"}, {"type": "choice", "answer": "B"}]
    r = grade_exam(["some answer", "B"], qs, 0.7)
    assert r.status == "pending_review"
    assert r.score is None

def test_wrong_answer_count():
    qs = [{"type": "choice", "answer": "B"}]
    r = grade_exam(["A", "B"], qs)
    assert r.status == "error"

# ── calculate_trust_delta ────────────────────────────────────

def test_trust_increment():
    old = {"code_quality": 0.5}
    new = calculate_trust_delta(old, "code_quality", +0.1)
    assert abs(new["code_quality"] - 0.6) < 0.001

def test_trust_clamps_to_one():
    old = {"code_quality": 0.95}
    new = calculate_trust_delta(old, "code_quality", +0.1)
    assert new["code_quality"] == 1.0

def test_trust_clamps_to_zero():
    old = {"code_quality": 0.02}
    new = calculate_trust_delta(old, "code_quality", -0.1)
    assert new["code_quality"] == 0.0

def test_trust_new_dimension():
    old = {}
    new = calculate_trust_delta(old, "test_quality", +0.1)
    assert abs(new["test_quality"] - 0.6) < 0.001  # default 0.5 + 0.1

def test_trust_immutable():
    old = {"x": 0.5}
    new = calculate_trust_delta(old, "x", +0.1)
    assert old["x"] == 0.5  # original not mutated

# ── analyze_rejection_trust ──────────────────────────────────

def test_basic_rejection_penalty():
    penalties = analyze_rejection_trust("bad code", [])
    assert len(penalties) == 1
    assert penalties[0][0] == "code_quality"

def test_fake_test_penalty():
    penalties = analyze_rejection_trust("bad", ["这是假测试"])
    dims = [p[0] for p in penalties]
    assert "test_quality" in dims

def test_scope_violation_penalty():
    penalties = analyze_rejection_trust("bad", ["scope violation: touched scripts/"])
    dims = [p[0] for p in penalties]
    assert "commit_discipline" in dims

def test_multiple_penalties():
    penalties = analyze_rejection_trust("bad", ["fake test", "scope issue"])
    assert len(penalties) == 3  # base + fake + scope

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


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

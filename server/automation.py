"""Aegis Automation — closes the 3 remaining gaps.

1. Canary Poller: auto-scrapes health_check_url on pre, feeds canary evaluation
2. Rollback Executor: SSH runs rollback_command when auto-rollback triggers
3. Agent Notifier: POSTs to agent webhook_url on ticket state changes
"""

import json
import logging
import threading
import time
import re

logger = logging.getLogger("aegis.automation")


# ── 1. Agent Notifier ────────────────────────────────────────

def notify_agent(db_conn, agent_id: str, event: str, payload: dict):
    """Send a webhook notification to an agent.

    Called when: ticket assigned, phase changed, review needed, etc.
    Fire-and-forget — never blocks the main flow.
    """
    agent = db_conn.execute("SELECT webhook_url FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not agent or not agent["webhook_url"]:
        return  # agent has no webhook — they poll instead

    url = agent["webhook_url"]
    data = json.dumps({"event": event, **payload}).encode()

    def _send():
        import urllib.request
        try:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
            logger.info(f"Notified {agent_id}: {event}")
        except Exception as e:
            logger.warning(f"Agent notification failed: {agent_id} → {e}")

    threading.Thread(target=_send, daemon=True).start()


def notify_project_agents(db_conn, project_id: str, event: str, payload: dict):
    """Notify all agents that have worked on this project."""
    agents = db_conn.execute(
        """SELECT DISTINCT a.id, a.webhook_url FROM agents a
           JOIN event_log e ON e.agent_id = a.id
           JOIN tickets t ON t.id = e.ticket_id
           WHERE t.project_id = ? AND a.webhook_url != ''""",
        (project_id,)).fetchall()
    for agent in agents:
        notify_agent(db_conn, agent["id"], event, payload)


# ── 2. Rollback Executor ─────────────────────────────────────

def execute_rollback(db_conn, project_id: str, ticket_id: str, env: str = "pre") -> dict:
    """SSH into the environment and run rollback_command.

    Called when canary health degrades or auto-rollback triggers.
    """
    from . import ci_runner
    from .db import log_event, now_ms

    proj = db_conn.execute("SELECT environments_json FROM projects WHERE id=?",
                           (project_id,)).fetchone()
    if not proj:
        return {"status": "error", "reason": "project not found"}

    envs = json.loads(proj["environments_json"] or "{}")
    env_cfg = envs.get(env, {})

    if not env_cfg.get("ssh_host") or not env_cfg.get("rollback_command"):
        return {"status": "skipped",
                "reason": f"environments.{env}.rollback_command not configured"}

    code, output = ci_runner._ssh_run(
        env_cfg["ssh_host"], env_cfg.get("ssh_user", "root"),
        env_cfg.get("ssh_port", 22), env_cfg.get("ssh_key_path", "~/.ssh/id_rsa"),
        env_cfg["rollback_command"],
        timeout=env_cfg.get("timeout_seconds", 300))

    log_event(db_conn, "rollback_executed" if code == 0 else "rollback_failed",
              ticket_id, "system", env, f"exit={code}")
    db_conn.commit()

    # Notify agents
    notify_project_agents(db_conn, project_id, "rollback", {
        "ticket_id": ticket_id, "env": env,
        "status": "ok" if code == 0 else "failed"})

    return {"status": "ok" if code == 0 else "failed",
            "env": env, "exit_code": code, "output": output[:500]}


# ── 3. Canary Poller ─────────────────────────────────────────

_poller_thread = None
_poller_stop = threading.Event()


def start_canary_poller(db_getter, interval_seconds: int = 60):
    """Start a background thread that auto-checks canary health.

    For every ticket in 'monitoring' phase:
      1. Read project's pre.health_check_url
      2. SSH curl the URL
      3. Parse response for metrics (error_rate, latency, etc.)
      4. Feed into canary evaluation
      5. Auto-promote or auto-rollback
    """
    global _poller_thread

    if _poller_thread and _poller_thread.is_alive():
        return  # already running

    def _poll_loop():
        logger.info(f"Canary poller started (interval={interval_seconds}s)")
        while not _poller_stop.is_set():
            try:
                _check_all_canaries(db_getter())
            except Exception as e:
                logger.error(f"Canary poller error: {e}")
            _poller_stop.wait(interval_seconds)
        logger.info("Canary poller stopped")

    _poller_stop.clear()
    _poller_thread = threading.Thread(target=_poll_loop, daemon=True)
    _poller_thread.start()


def stop_canary_poller():
    """Stop the background canary poller."""
    _poller_stop.set()


def _check_all_canaries(db_conn):
    """Check all tickets in monitoring phase."""
    from . import ci_runner, logic
    from .db import log_event, now_ms

    tickets = db_conn.execute(
        "SELECT * FROM tickets WHERE phase='monitoring'").fetchall()

    for t in tickets:
        if not t["project_id"]:
            continue

        proj = db_conn.execute(
            "SELECT environments_json,webhook_url FROM projects WHERE id=?",
            (t["project_id"],)).fetchone()
        if not proj:
            continue

        envs = json.loads(proj["environments_json"] or "{}")
        pre_cfg = envs.get("pre", {})

        if not pre_cfg.get("ssh_host") or not pre_cfg.get("health_check_url"):
            continue  # can't auto-check without health URL

        # SSH curl the health endpoint
        code, output = ci_runner._ssh_run(
            pre_cfg["ssh_host"], pre_cfg.get("ssh_user", "root"),
            pre_cfg.get("ssh_port", 22), pre_cfg.get("ssh_key_path", "~/.ssh/id_rsa"),
            f"curl -sf {pre_cfg['health_check_url']} 2>/dev/null || echo 'HEALTH_CHECK_FAILED'",
            timeout=15)

        # Parse metrics from health response
        metrics = _parse_health_response(output)
        now = now_ms()

        # Build MetricsSnapshot
        current = logic.MetricsSnapshot(
            error_rate=metrics.get("error_rate", 0.0),
            latency_p50_ms=metrics.get("latency_p50_ms", 0.0),
            latency_p99_ms=metrics.get("latency_p99_ms", 0.0),
            request_rate=metrics.get("request_rate", 0.0),
            saturation=metrics.get("saturation", 0.0),
            timestamp_ms=now)

        health = logic.evaluate_health(current, baseline=None)

        # Check if we should promote
        stages = json.loads(t["canary_plan"] or "[]") or [25, 100]
        current_stage = t["canary_stage"] or stages[0]

        promote = logic.should_promote_canary(
            current_stage, stages, metrics.get("error_rate", 0.0),
            metrics.get("latency_p99_ms", 0.0))

        if promote.ok and promote.data.get("action") == "promote":
            next_stage = promote.data["to"]
            db_conn.execute("UPDATE tickets SET canary_stage=?,updated_at=? WHERE id=?",
                          (next_stage, now, t["id"]))
            log_event(db_conn, "canary_auto_promoted", t["id"], "system",
                     str(current_stage), str(next_stage))

            if next_stage >= 100:
                db_conn.execute("UPDATE tickets SET phase='done',canary_stage=100,updated_at=? WHERE id=?",
                              (now, t["id"]))
                log_event(db_conn, "canary_complete", t["id"], "system", "monitoring", "done")
                # Auto-deploy to prod
                _auto_deploy_from_poller(db_conn, t["project_id"], "prod")

            notify_project_agents(db_conn, t["project_id"], "canary_promoted", {
                "ticket_id": t["id"], "from": current_stage, "to": next_stage})

        # Check if we should rollback
        elif not health.ok:
            rollback_check = logic.should_auto_rollback(
                metrics.get("error_rate", 0.0),
                metrics.get("latency_p99_ms", 0.0),
                error_budget=0.01)
            if rollback_check.should_rollback:
                logger.warning(f"Auto-rollback triggered for {t['id']}: {rollback_check.reason}")
                execute_rollback(db_conn, t["project_id"], t["id"], "pre")
                # Move ticket back to rework
                db_conn.execute("UPDATE tickets SET phase='rework',canary_stage=0,updated_at=? WHERE id=?",
                              (now, t["id"]))
                log_event(db_conn, "auto_rollback", t["id"], "system", "monitoring", "rework")

        db_conn.commit()


def _auto_deploy_from_poller(db_conn, project_id: str, env: str):
    """Deploy from poller context (reuse ci_runner SSH)."""
    from . import ci_runner
    from .db import log_event

    proj = db_conn.execute("SELECT environments_json FROM projects WHERE id=?",
                           (project_id,)).fetchone()
    if not proj:
        return

    envs = json.loads(proj["environments_json"] or "{}")
    env_cfg = envs.get(env, {})
    if not env_cfg.get("ssh_host") or not env_cfg.get("deploy_command"):
        return

    code, output = ci_runner._ssh_run(
        env_cfg["ssh_host"], env_cfg.get("ssh_user", "root"),
        env_cfg.get("ssh_port", 22), env_cfg.get("ssh_key_path", "~/.ssh/id_rsa"),
        env_cfg["deploy_command"],
        timeout=env_cfg.get("timeout_seconds", 300))

    log_event(db_conn, "auto_deployed" if code == 0 else "auto_deploy_failed",
              project_id, "system", "", env)


def _parse_health_response(output: str) -> dict:
    """Parse a health check response into metrics.

    Supports JSON responses like: {"status":"ok","error_rate":0.01,"latency_p99_ms":120}
    Falls back to heuristics for non-JSON responses.
    """
    metrics = {"error_rate": 0.0, "latency_p50_ms": 0.0,
               "latency_p99_ms": 0.0, "request_rate": 0.0, "saturation": 0.0}

    if "HEALTH_CHECK_FAILED" in output:
        metrics["error_rate"] = 1.0
        return metrics

    # Try JSON parse
    try:
        data = json.loads(output.strip())
        for key in metrics:
            if key in data:
                metrics[key] = float(data[key])
        # If status is not ok, mark high error rate
        if data.get("status") not in ("ok", "healthy", "up", True):
            metrics["error_rate"] = max(metrics["error_rate"], 0.5)
        return metrics
    except (json.JSONDecodeError, ValueError):
        pass

    # Non-JSON: if we got a response, assume it's healthy
    if output.strip():
        return metrics

    # Empty response = unhealthy
    metrics["error_rate"] = 1.0
    return metrics

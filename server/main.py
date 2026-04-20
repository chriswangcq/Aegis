"""Aegis — AI-native engineering governance platform."""
import json, logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from .db import get_db, init_schema, seed_roles, now_ms, log_event, PHASE_ROLE, SUBMIT_NEXT, VALID_PHASES
from .models import *
from . import logic
from . import provisioner
from . import automation

logger = logging.getLogger("command-center")
_conn = None
def db(): return _conn

def row_to_dict(row): return dict(row) if row else None

def _pj(d):
    if not d: return d
    for k in ("depends_on","scope_json","checklist_json","trust_json","capabilities","refs","exam_json","exam_answers"):
        if k in d and isinstance(d[k], str):
            try: d[k] = json.loads(d[k])
            except: pass
    return d

def _trust(agent_id, role_id, ticket_id, dim, delta, reason, priority=3):
    """Record trust event with priority-weighted delta."""
    weighted_delta = logic.weight_by_priority(delta, priority)
    now = now_ms()
    db().execute("INSERT INTO trust_events (agent_id,role_id,ticket_id,dimension,delta,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                 (agent_id, role_id, ticket_id, dim, weighted_delta, f"{reason} (p{priority})", now))
    cert = db().execute("SELECT trust_json FROM certifications WHERE agent_id=? AND role_id=?", (agent_id, role_id)).fetchone()
    if cert:
        t = json.loads(cert["trust_json"]) if cert["trust_json"] else {}
        t[dim] = max(0.0, min(1.0, t.get(dim, 0.5) + weighted_delta))
        db().execute("UPDATE certifications SET trust_json=?, updated_at=? WHERE agent_id=? AND role_id=?",
                     (json.dumps(t), now, agent_id, role_id))

@asynccontextmanager
async def lifespan(app):
    global _conn
    _conn = get_db(); init_schema(_conn); seed_roles(_conn)
    automation.start_canary_poller(db, interval_seconds=60)
    logger.info("Aegis v1.0 ready")
    yield
    automation.stop_canary_poller()
    _conn.close()

app = FastAPI(title="Aegis", description="AI-native engineering governance platform", version="1.0.0", lifespan=lifespan)

_dashboard_dir = Path(__file__).parent.parent / "dashboard"
if _dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir)), name="dashboard")

@app.get("/", include_in_schema=False)
def root_redirect():
    return FileResponse(str(_dashboard_dir / "index.html"))

@app.get("/status")
def health():
    """Health check for Docker / load balancer."""
    projects = db().execute("SELECT COUNT(*) as c FROM projects").fetchone()["c"]
    tickets = db().execute("SELECT COUNT(*) as c FROM tickets").fetchone()["c"]
    agents = db().execute("SELECT COUNT(*) as c FROM agents").fetchone()["c"]
    return {"status": "ok", "version": "1.0.0", "projects": projects,
            "tickets": tickets, "agents": agents}

# ── ROLES & CERTIFICATION ────────────────────────────────────

@app.get("/roles")
def list_roles():
    return {"roles": [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM roles").fetchall()]}

@app.get("/roles/{role_id}")
def get_role(role_id: str):
    r = db().execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not r: raise HTTPException(404)
    return _pj(row_to_dict(r))

@app.get("/roles/{role_id}/exam")
def get_exam(role_id: str):
    """Worker reads exam questions before attempting certification."""
    r = db().execute("SELECT exam_json FROM roles WHERE id=?", (role_id,)).fetchone()
    if not r: raise HTTPException(404)
    exam = json.loads(r["exam_json"]) if r["exam_json"] else []
    # Strip answers/criteria from response — worker shouldn't see them
    safe = []
    for i, q in enumerate(exam):
        item = {"index": i, "question": q["q"], "type": q.get("type", "open")}
        if q.get("options"): item["options"] = q["options"]
        safe.append(item)
    return {"role_id": role_id, "questions": safe, "count": len(safe)}

@app.post("/roles/{role_id}/exam")
def submit_exam(role_id: str, body: ExamSubmit):
    """Worker submits exam answers. Master/system grades them."""
    now = now_ms()
    role = db().execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not role: raise HTTPException(404)
    agent = db().execute("SELECT id FROM agents WHERE id=?", (body.agent_id,)).fetchone()
    if not agent: raise HTTPException(404, "Agent not registered")

    exam = json.loads(role["exam_json"]) if role["exam_json"] else []
    if len(body.answers) != len(exam):
        raise HTTPException(400, f"Expected {len(exam)} answers, got {len(body.answers)}")

    # Auto-grade choice questions, mark open questions as pending_review
    results = []; auto_score = 0; auto_total = 0
    for i, (q, ans) in enumerate(zip(exam, body.answers)):
        if q.get("type") == "choice":
            correct = ans.strip().upper().startswith(q.get("answer", "").upper())
            results.append({"index": i, "correct": correct, "auto_graded": True})
            auto_total += 1
            if correct: auto_score += 1
        else:
            results.append({"index": i, "answer": ans, "auto_graded": False, "status": "pending_review"})

    # If all questions are choice-type, we can auto-certify
    has_open = any(not r.get("auto_graded") for r in results)
    if not has_open and auto_total > 0:
        score = auto_score / auto_total
        status = "passed" if score >= (role["min_pass_score"] or 0.7) else "failed"
    else:
        score = None
        status = "pending_review"  # Master needs to grade open questions

    trust = {"code_quality": 0.5, "test_quality": 0.5, "commit_discipline": 0.5, "thoroughness": 0.5}
    db().execute(
        "INSERT OR REPLACE INTO certifications (agent_id,role_id,status,score,exam_answers,trust_json,certified_at,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (body.agent_id, role_id, status, score, json.dumps(body.answers), json.dumps(trust),
         now if status == "passed" else None, now, now))
    log_event(db(), "exam_submitted", agent_id=body.agent_id, new_value=f"{role_id}:{status}")
    db().commit()
    return {"agent_id": body.agent_id, "role_id": role_id, "status": status, "score": score, "results": results}

@app.post("/certifications/{agent_id}/{role_id}/grade")
def grade_certification(agent_id: str, role_id: str, score: float, verdict: str = "passed"):
    """Master grades open-ended exam answers."""
    now = now_ms()
    cert = db().execute("SELECT * FROM certifications WHERE agent_id=? AND role_id=?", (agent_id, role_id)).fetchone()
    if not cert: raise HTTPException(404)
    status = verdict if verdict in ("passed", "failed") else "passed"
    db().execute("UPDATE certifications SET status=?, score=?, certified_at=?, updated_at=? WHERE agent_id=? AND role_id=?",
                 (status, score, now if status == "passed" else None, now, agent_id, role_id))
    log_event(db(), "exam_graded", agent_id=agent_id, new_value=f"{role_id}:{status}:{score}")
    db().commit()
    return {"agent_id": agent_id, "role_id": role_id, "status": status, "score": score}

@app.get("/certifications/{agent_id}")
def get_certifications(agent_id: str):
    rows = db().execute("SELECT * FROM certifications WHERE agent_id=?", (agent_id,)).fetchall()
    return {"agent_id": agent_id, "certifications": [_pj(row_to_dict(r)) for r in rows]}

# ── AGENTS ───────────────────────────────────────────────────

@app.get("/agents")
def list_agents():
    agents = []
    for a in db().execute("SELECT * FROM agents ORDER BY id").fetchall():
        d = row_to_dict(a)
        certs = db().execute("SELECT * FROM certifications WHERE agent_id=?", (a["id"],)).fetchall()
        d["certifications"] = [_pj(row_to_dict(c)) for c in certs]
        d["certified_roles"] = [{"role": c["role_id"], "score": c["score"]} for c in certs if c["status"] == "passed"]
        agents.append(d)
    return {"agents": agents}

@app.post("/agents")
def register_agent(body: AgentRegister):
    now = now_ms()
    db().execute("INSERT OR REPLACE INTO agents (id,display_name,provider,webhook_url,status,created_at,updated_at) VALUES(?,?,?,?,'idle',?,?)",
                 (body.id, body.display_name or body.id, body.provider, body.webhook_url, now, now))
    log_event(db(), "agent_registered", agent_id=body.id)
    db().commit()
    return {"id": body.id, "next_step": "GET /roles to see available roles, then GET /roles/{id}/exam to take certification exam"}

@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    a = db().execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not a: raise HTTPException(404)
    d = row_to_dict(a)
    d["certifications"] = [_pj(row_to_dict(c)) for c in
        db().execute("SELECT * FROM certifications WHERE agent_id=?", (agent_id,)).fetchall()]
    return d

@app.post("/agents/{agent_id}/heartbeat")
def heartbeat(agent_id: str):
    now = now_ms()
    a = db().execute("SELECT current_ticket FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not a: raise HTTPException(404)
    db().execute("UPDATE agents SET last_active_at=?, updated_at=? WHERE id=?", (now, now, agent_id))
    if a["current_ticket"]:
        db().execute("UPDATE tickets SET locked_at=?, updated_at=? WHERE id=? AND assigned_to=?",
                     (now, now, a["current_ticket"], agent_id))
    db().commit()
    return {"ok": True, "refreshed": a["current_ticket"]}

# ── TICKETS ──────────────────────────────────────────────────

@app.get("/tickets")
def list_tickets(phase: str = None, project_id: str = None, available: bool = False):
    conditions = []
    params = []
    if project_id:
        conditions.append("project_id=?")
        params.append(project_id)
    if available:
        claim_phases = list(PHASE_ROLE.keys())
        placeholders = ",".join("?" * len(claim_phases))
        conditions.append(f"phase IN ({placeholders})")
        params.extend(claim_phases)
        conditions.append("blocked_by IS NULL")
        conditions.append("assigned_to IS NULL")
    elif phase:
        conditions.append("phase=?")
        params.append(phase)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = db().execute(f"SELECT * FROM tickets {where} ORDER BY priority DESC, created_at ASC", params).fetchall()
    return {"tickets": [_pj(row_to_dict(r)) for r in rows], "count": len(rows)}

@app.get("/tickets/{tid}")
def get_ticket(tid: str):
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    r = _pj(row_to_dict(t))
    r["evidence"] = [row_to_dict(e) for e in db().execute("SELECT * FROM evidence WHERE ticket_id=? ORDER BY timestamp", (tid,)).fetchall()]
    r["comments"] = [_pj(row_to_dict(c)) for c in db().execute("SELECT * FROM comments WHERE ticket_id=? ORDER BY created_at", (tid,)).fetchall()]
    r["open_blockers"] = db().execute("SELECT COUNT(*) as c FROM comments WHERE ticket_id=? AND comment_type='blocker' AND status='open'", (tid,)).fetchone()["c"]
    return r

# ── Projects ─────────────────────────────────────────────────

@app.post("/projects")
def create_project(body: ProjectCreate):
    now = now_ms()
    envs = body.environments.model_dump()
    import sqlite3
    try:
        db().execute(
            "INSERT INTO projects (id,name,description,repo_url,tech_stack,conventions,environments_json,default_domain,master_id,metrics_url,webhook_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.id, body.name, body.description, body.repo_url,
             json.dumps(body.tech_stack), json.dumps(body.conventions),
             json.dumps(envs),
             body.default_domain, body.master_id, body.metrics_url, body.webhook_url, now, now))
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Project '{body.id}' already exists")
    log_event(db(), "project_created", body.id, body.master_id)
    db().commit()
    # Auto-provision: API keys
    result = provisioner.provision_project(body.id, body.master_id, db())
    return {"id": body.id, "name": body.name, "master_id": body.master_id,
            "repo_url": body.repo_url,
            "api_keys": result.api_keys,
            "environments": envs}

@app.get("/projects")
def list_projects(status: str = "active"):
    rows = db().execute("SELECT * FROM projects WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
    return {"projects": [_pj(row_to_dict(r)) for r in rows]}

@app.get("/projects/{pid}")
def get_project(pid: str):
    p = db().execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p: raise HTTPException(404)
    r = _pj(row_to_dict(p))
    # Project dashboard
    tickets = db().execute("SELECT id,title,phase,priority,assigned_to,domain FROM tickets WHERE project_id=? ORDER BY priority DESC", (pid,)).fetchall()
    r["tickets"] = [row_to_dict(t) for t in tickets]
    r["ticket_summary"] = {}
    for t in tickets:
        phase = t["phase"]
        r["ticket_summary"][phase] = r["ticket_summary"].get(phase, 0) + 1
    # Project-level DORA
    events = [row_to_dict(e) for e in db().execute(
        "SELECT * FROM event_log WHERE ticket_id IN (SELECT id FROM tickets WHERE project_id=?) ORDER BY timestamp",
        (pid,)).fetchall()]
    if events:
        metrics = logic.calculate_dora(events, now_ms(), 30)
        r["dora"] = {
            "deployment_frequency": metrics.deployment_frequency,
            "lead_time_ms": metrics.lead_time_ms,
            "change_failure_rate": metrics.change_failure_rate,
            "mttr_ms": metrics.mttr_ms,
        }
    return r

@app.patch("/projects/{pid}")
def update_project(pid: str, body: dict):
    """Update project settings (environments, conventions, etc.)."""
    p = db().execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p: raise HTTPException(404)
    now = now_ms()
    allowed = {"environments", "conventions", "metrics_url", "webhook_url",
               "tech_stack", "default_domain", "description", "master_id"}
    updates = []
    params = []
    for key, val in body.items():
        if key not in allowed:
            raise HTTPException(400, f"Cannot update '{key}'")
        col = "environments_json" if key == "environments" else key
        if isinstance(val, (dict, list)):
            val = json.dumps(val)
        updates.append(f"{col}=?")
        params.append(val)
    if not updates:
        raise HTTPException(400, "No valid fields to update")
    params.extend([now, pid])
    db().execute(f"UPDATE projects SET {','.join(updates)},updated_at=? WHERE id=?", params)
    log_event(db(), "project_updated", pid, "", "", json.dumps(list(body.keys())))
    db().commit()
    return {"id": pid, "updated": list(body.keys())}

# ── Tickets ──────────────────────────────────────────────────

@app.post("/tickets")
def create_ticket(body: TicketCreate):
    now = now_ms()
    cl = [{"description": c, "status": "pending"} for c in body.checklist]
    scope = {}
    if body.scope_includes: scope["includes"] = body.scope_includes
    if body.scope_excludes: scope["excludes"] = body.scope_excludes
    blocked_by = None
    for dep in body.depends_on:
        row = db().execute("SELECT phase FROM tickets WHERE id=?", (dep,)).fetchone()
        if row and row["phase"] != "done": blocked_by = dep; break
    phase = "ready" if not blocked_by else "planning"

    # Inherit project defaults if project_id specified
    project = None
    domain = body.domain
    if body.project_id:
        project = db().execute("SELECT * FROM projects WHERE id=?", (body.project_id,)).fetchone()
        if not project: raise HTTPException(404, f"Project '{body.project_id}' not found")
        if not domain and project["default_domain"]:
            domain = project["default_domain"]

    # Store skip_preflight in scope_json
    if body.skip_preflight:
        scope["skip_preflight"] = True
    # Gap 3: detect if design_review needed
    needs_rfc = logic.should_require_design_review(
        body.risk_level, body.priority,
        scope_includes=body.scope_includes if body.scope_includes else None)
    if needs_rfc:
        scope["require_design_review"] = True
    # Calculate canary plan based on risk
    canary = logic.calculate_canary_plan(body.risk_level, body.priority)
    db().execute("INSERT INTO tickets (id,project_id,title,description,phase,depends_on,blocked_by,scope_json,checklist_json,test_specs_json,priority,risk_level,domain,canary_plan,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (body.id, body.project_id or None, body.title, body.description, phase, json.dumps(body.depends_on), blocked_by, json.dumps(scope), json.dumps(cl), json.dumps(body.test_specs), body.priority, body.risk_level, domain, json.dumps(canary.stages), body.created_by, now, now))
    log_event(db(), "ticket_created", body.id, new_value=phase); db().commit()
    return {"id": body.id, "project_id": body.project_id or None, "phase": phase, "blocked_by": blocked_by,
            "skip_preflight": body.skip_preflight, "design_review_required": needs_rfc,
            "domain": domain, "test_specs": len(body.test_specs),
            "canary_plan": canary.stages}

@app.post("/tickets/{tid}/claim")
def claim_ticket(tid: str, body: TicketClaim):
    now = now_ms()
    # ── I/O: fetch data ──
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    required_role = PHASE_ROLE.get(t["phase"], "coder")
    cert = db().execute("SELECT status, expires_at FROM certifications WHERE agent_id=? AND role_id=? ORDER BY updated_at DESC LIMIT 1",
                        (body.agent_id, required_role)).fetchone()

    # ── Logic: pure function ──
    ticket_dict = dict(t)
    ticket_dict["scope_json"] = json.loads(t["scope_json"]) if t["scope_json"] else {}
    result = logic.can_claim(ticket_dict, body.agent_id, dict(cert) if cert else None, now, PHASE_ROLE)
    if not result.ok:
        # Side effect: expire certification if that was the failure reason
        if cert and cert["expires_at"] and cert["expires_at"] < now:
            db().execute("UPDATE certifications SET status='expired', updated_at=? WHERE agent_id=? AND role_id=?",
                         (now, body.agent_id, required_role))
            db().commit()
        raise HTTPException(403 if "certified" in result.error or "expired" in result.error else 409, result.error)

    # ── Logic: anti-self-review ──
    if required_role == "reviewer":
        # Check ALL impl/rework evidence, not just first row (vuln 3 fix)
        coder_evs = db().execute(
            "SELECT DISTINCT e.agent_id, a.provider FROM evidence e JOIN agents a ON a.id=e.agent_id "
            "WHERE e.ticket_id=? AND e.phase IN ('implementation','rework','preflight_rework')",
            (tid,)).fetchall()
        my_provider = db().execute("SELECT provider FROM agents WHERE id=?", (body.agent_id,)).fetchone()
        for cev in (coder_evs or []):
            review_check = logic.can_review(
                body.agent_id, my_provider["provider"] if my_provider else "",
                cev["agent_id"], cev["provider"])
            if not review_check.ok:
                raise HTTPException(403, review_check.error)

    # ── I/O: write ──
    next_phase = result.data["next_phase"]
    role = result.data["role"]
    res = db().execute("UPDATE tickets SET phase=?,assigned_to=?,assigned_role=?,locked_at=?,updated_at=? WHERE id=? AND (phase=? OR (locked_at IS NOT NULL AND locked_at+lock_ttl_ms<?))",
                       (next_phase, body.agent_id, role, now, now, tid, t["phase"], now))
    if res.rowcount == 0: raise HTTPException(409, "Race: claimed by someone else")
    db().execute("UPDATE agents SET status='busy',current_ticket=?,current_role=?,last_active_at=?,updated_at=? WHERE id=?",
                 (tid, role, now, now, body.agent_id))
    log_event(db(), "claimed", tid, body.agent_id, t["phase"], next_phase); db().commit()
    # Notify agent they've been assigned
    automation.notify_agent(db(), body.agent_id, "ticket_claimed", {
        "ticket_id": tid, "phase": next_phase, "role": role})
    return {"ticket_id": tid, "role": role, "phase": next_phase, "agent_id": body.agent_id}

@app.post("/tickets/{tid}/submit")
def submit_ticket(tid: str, body: TicketSubmit):
    now = now_ms()
    # ── I/O: fetch ──
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    if t["assigned_to"] != body.agent_id: raise HTTPException(403)
    phase = t["phase"]; next_phase = SUBMIT_NEXT.get(phase)
    if not next_phase: raise HTTPException(409, f"Cannot submit from '{phase}'")
    bl = db().execute("SELECT COUNT(*) as c FROM comments WHERE ticket_id=? AND comment_type='blocker' AND status='open'", (tid,)).fetchone()["c"]
    if bl > 0: raise HTTPException(409, f"{bl} unresolved blocker(s)")

    checklist = json.loads(t["checklist_json"]) if t["checklist_json"] else []
    try:
        test_specs = json.loads(t["test_specs_json"]) if t["test_specs_json"] else []
    except (IndexError, KeyError):
        test_specs = []
    ci_results = []

    # ── Aegis CI: SSH-based verification ──
    if phase in ("implementation", "rework"):
        if not t["project_id"]:
            raise HTTPException(400, "Ticket must belong to a project")
        proj = db().execute("SELECT repo_url, environments_json FROM projects WHERE id=?",
                            (t["project_id"],)).fetchone()
        if not proj or not proj["repo_url"]:
            raise HTTPException(400, "Project must have repo_url")
        if not body.branch and not body.commit_sha:
            raise HTTPException(400, "Must submit branch or commit_sha (git push first)")

        envs = json.loads(proj["environments_json"] or "{}") if proj["environments_json"] else {}
        ci_env = envs.get("ci", {})
        if not ci_env.get("ssh_host"):
            raise HTTPException(400, "Project environments.ci.ssh_host not configured")

        from . import ci_runner
        ci_results = ci_runner.run_ci_via_ssh(
            proj["repo_url"], branch=body.branch or "main",
            commit_sha=body.commit_sha,
            ci_config=ci_env,
            test_specs=test_specs, checklist=checklist
        )

        failed = [r for r in ci_results if not r.passed]
        if failed:
            details = [{"gate": r.gate, "detail": r.detail, "output": r.output[:500]} for r in failed]
            raise HTTPException(400, {
                "message": f"{len(failed)} CI gate(s) failed",
                "failed_gates": details,
                "verification_mode": "ssh_remote",
                "hint": "Aegis SSHed into the CI server and ran these checks."
            })
    else:
        # Non-impl phases: validate evidence normally
        ev_dicts = [{"evidence_type": ev.evidence_type, "content": ev.content, "verdict": ev.verdict} for ev in body.evidence]
        # Gap 1: monitoring phase requires health_check + error_rate evidence
        if phase == "monitoring":
            mon_check = logic.validate_monitoring_evidence(ev_dicts)
            if not mon_check.ok:
                raise HTTPException(400, mon_check.error)
        else:
            ev_check = logic.validate_submit_evidence(phase, ev_dicts, checklist=checklist)
            if not ev_check.ok:
                raise HTTPException(400, ev_check.error)

    # ── I/O: write ──
    # Record agent-submitted evidence
    for ev in body.evidence:
        db().execute("INSERT INTO evidence (ticket_id,phase,agent_id,evidence_type,content,verdict,timestamp) VALUES(?,?,?,?,?,?,?)",
                     (tid, phase, body.agent_id, ev.evidence_type, ev.content, ev.verdict, now))
    # Record CI results as system evidence (tamper-proof)
    for r in ci_results:
        db().execute("INSERT INTO evidence (ticket_id,phase,agent_id,evidence_type,content,verdict,timestamp) VALUES(?,?,?,?,?,?,?)",
                     (tid, phase, "system", f"ci_{r.gate}", f"{r.detail}\n{r.output[:2000]}", "pass" if r.passed else "fail", now))

    db().execute("UPDATE tickets SET phase=?,assigned_to=NULL,assigned_role=NULL,locked_at=NULL,updated_at=? WHERE id=?", (next_phase, now, tid))
    db().execute("UPDATE agents SET status='idle',current_ticket=NULL,current_role=NULL,updated_at=? WHERE id=?", (now, body.agent_id))
    role = t["assigned_role"] or "coder"
    priority = t["priority"] or 3
    _trust(body.agent_id, role, tid, "commit_discipline", +0.02, f"clean submit from {phase}", priority=priority)
    db().execute("UPDATE certifications SET tasks_completed=tasks_completed+1, updated_at=? WHERE agent_id=? AND role_id=?", (now, body.agent_id, role))
    log_event(db(), "submitted", tid, body.agent_id, phase, next_phase); db().commit()
    passed_gates = [r.gate for r in ci_results if r.passed]
    vmode = "system_executed" if ci_results else "evidence"
    return {"ticket_id": tid, "previous_phase": phase, "new_phase": next_phase,
            "verification_mode": vmode,
            "gates_passed": passed_gates}

@app.post("/tickets/{tid}/reject")
def reject_ticket(tid: str, body: TicketReject):
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    # Auth: reject requires a reviewer who is assigned to this ticket, or master
    if body.agent_id:
        if t["assigned_to"] and t["assigned_to"] != body.agent_id:
            raise HTTPException(403, f"Only assigned reviewer '{t['assigned_to']}' or master can reject")
    reject_map = {"preflight_review": "preflight_rework", "code_review": "rework", "qa": "rework", "merge_ready": "rework"}
    next_phase = reject_map.get(t["phase"])
    if not next_phase: raise HTTPException(409)
    rejector = body.agent_id or t["assigned_to"] or "master"
    for bc in body.blocker_comments:
        db().execute("INSERT INTO comments (ticket_id,author_id,author_role,content,comment_type,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     (tid, rejector, t["assigned_role"] or "reviewer", bc, "blocker", "open", now, now))
    rounds = (t["review_rounds"] or 0) + 1
    db().execute("UPDATE tickets SET phase=?,assigned_to=NULL,assigned_role=NULL,locked_at=NULL,review_rounds=?,updated_at=? WHERE id=?", (next_phase, rounds, now, tid))
    # Penalize the original coder, not the reviewer
    coder_ev = db().execute(
        "SELECT DISTINCT e.agent_id, e.phase FROM evidence e WHERE e.ticket_id=? AND e.phase IN ('implementation','rework')",
        (tid,)).fetchone()
    coder_id = coder_ev["agent_id"] if coder_ev else None
    if coder_id:
        coder_role = "coder"
        _trust(coder_id, coder_role, tid, "code_quality", -0.03, f"rejected: {body.reason[:50]}")
        db().execute("UPDATE certifications SET tasks_failed=tasks_failed+1, updated_at=? WHERE agent_id=? AND role_id=?", (now, coder_id, coder_role))
    if t["assigned_to"]:
        db().execute("UPDATE agents SET status='idle',current_ticket=NULL,current_role=NULL,updated_at=? WHERE id=?", (now, t["assigned_to"]))
    log_event(db(), "rejected", tid, rejector, t["phase"], next_phase, json.dumps({"reason": body.reason, "round": rounds}))

    # Gap 2: Auto-trigger post-mortem when review_rounds >= 2
    post_mortem = None
    if rounds >= 2:
        all_blockers = [c["content"] for c in db().execute(
            "SELECT content FROM comments WHERE ticket_id=? AND comment_type='blocker'",
            (tid,)).fetchall()]
        pm = logic.analyze_post_mortem(rounds, all_blockers)
        if pm.should_trigger:
            db().execute(
                "INSERT INTO post_mortems (ticket_id,trigger_reason,pattern,action_items,created_at) VALUES(?,?,?,?,?)",
                (tid, pm.reason, ",".join(pm.patterns), json.dumps(pm.action_items), now))
            post_mortem = {"patterns": pm.patterns, "action_items": pm.action_items}

    db().commit()
    result = {"ticket_id": tid, "phase": next_phase, "review_round": rounds, "rejected_by": rejector}
    if post_mortem:
        result["post_mortem"] = post_mortem
    return result

@app.post("/tickets/{tid}/advance")
def advance_ticket(tid: str, body: TicketAdvance):
    """Only master-certified agents can advance tickets."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    if body.target_phase not in VALID_PHASES: raise HTTPException(400)
    # Auth: advance is a master-only action (skip check if no agent_id for backward compat)
    if hasattr(body, 'agent_id') and body.agent_id:
        master_cert = db().execute(
            "SELECT status FROM certifications WHERE agent_id=? AND role_id='master' AND status='passed'",
            (body.agent_id,)).fetchone()
        if not master_cert:
            raise HTTPException(403, "Only master-certified agents can advance tickets")
    old = t["phase"]
    db().execute("UPDATE tickets SET phase=?,assigned_to=NULL,locked_at=NULL,updated_at=? WHERE id=?", (body.target_phase, now, tid))
    unblocked = []
    if body.target_phase == "done":
        for dep in db().execute("SELECT id FROM tickets WHERE blocked_by=?", (tid,)).fetchall():
            db().execute("UPDATE tickets SET blocked_by=NULL,phase='ready',updated_at=? WHERE id=?", (now, dep["id"]))
            unblocked.append(dep["id"])
    log_event(db(), "advanced", tid, old_value=old, new_value=body.target_phase); db().commit()
    r = {"ticket_id": tid, "phase": body.target_phase}
    if unblocked: r["unblocked"] = unblocked

    # Auto-deploy to pre when entering monitoring (canary start)
    if body.target_phase == "monitoring" and t["project_id"]:
        deploy_result = _auto_deploy(t["project_id"], "pre")
        r["deploy"] = deploy_result

    # Notify: needs_review → notify all certified reviewers
    if body.target_phase == "code_review":
        reviewers = db().execute(
            "SELECT a.id FROM agents a JOIN certifications c ON a.id=c.agent_id "
            "WHERE c.role_id='reviewer' AND c.status='passed' AND a.webhook_url!=''").fetchall()
        for rv in reviewers:
            automation.notify_agent(db(), rv["id"], "review_needed", {
                "ticket_id": tid, "project_id": t["project_id"] or ""})

    # Notify project agents on deploy/done events
    if body.target_phase in ("monitoring", "done") and t["project_id"]:
        automation.notify_project_agents(db(), t["project_id"], "phase_changed", {
            "ticket_id": tid, "phase": body.target_phase})

    return r

@app.post("/tickets/{tid}/release")
def release_ticket(tid: str, body: TicketClaim):
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    revert = {"preflight":"ready","implementation":"implementation","preflight_rework":"ready","rework":"code_review",
              "preflight_review":"preflight_review","code_review":"code_review","qa":"qa","deploy_prep":"deploy_prep"}
    prev = revert.get(t["phase"], "ready")
    db().execute("UPDATE tickets SET phase=?,assigned_to=NULL,assigned_role=NULL,locked_at=NULL,updated_at=? WHERE id=?", (prev, now, tid))
    db().execute("UPDATE agents SET status='idle',current_ticket=NULL,current_role=NULL,updated_at=? WHERE id=?", (now, body.agent_id))
    log_event(db(), "released", tid, body.agent_id, t["phase"], prev); db().commit()
    return {"ticket_id": tid, "phase": prev}

@app.patch("/tickets/{tid}/checklist/{index}")
def update_checklist(tid: str, index: int, status: str = "done"):
    now = now_ms()
    t = db().execute("SELECT checklist_json FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    cl = json.loads(t["checklist_json"]) if t["checklist_json"] else []
    if index < 0 or index >= len(cl): raise HTTPException(400)
    cl[index]["status"] = status
    db().execute("UPDATE tickets SET checklist_json=?,updated_at=? WHERE id=?", (json.dumps(cl), now, tid)); db().commit()
    return {"ticket_id": tid, "index": index, "item": cl[index]["description"], "status": status}

# ── COMMENTS ─────────────────────────────────────────────────

@app.get("/tickets/{tid}/comments")
def list_comments(tid: str):
    return {"comments": [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM comments WHERE ticket_id=? ORDER BY created_at", (tid,)).fetchall()]}

@app.post("/tickets/{tid}/comments")
def create_comment(tid: str, body: CommentCreate):
    now = now_ms()
    cur = db().execute("INSERT INTO comments (ticket_id,author_id,author_role,content,comment_type,status,refs,parent_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (tid, body.author_id, body.author_role, body.content, body.comment_type, "open", json.dumps(body.refs), body.parent_id, now, now))
    db().commit()
    return {"id": cur.lastrowid, "ticket_id": tid}

@app.patch("/tickets/{tid}/comments/{cid}")
def update_comment(tid: str, cid: int, body: CommentUpdate):
    now = now_ms()
    db().execute("UPDATE comments SET status=?,updated_at=? WHERE id=? AND ticket_id=?", (body.status, now, cid, tid)); db().commit()
    return {"id": cid, "status": body.status}

# ── KNOWLEDGE ────────────────────────────────────────────────

@app.get("/knowledge")
def list_knowledge(category: str = None, q: str = None):
    if q: rows = db().execute("SELECT * FROM knowledge WHERE title LIKE ? OR content LIKE ?", (f"%{q}%", f"%{q}%")).fetchall()
    elif category: rows = db().execute("SELECT * FROM knowledge WHERE category=?", (category,)).fetchall()
    else: rows = db().execute("SELECT * FROM knowledge ORDER BY category").fetchall()
    return {"items": [_pj(row_to_dict(r)) for r in rows]}

@app.post("/knowledge")
def create_knowledge(body: KnowledgeCreate):
    now = now_ms()
    db().execute("INSERT OR REPLACE INTO knowledge (id,category,title,content,tags,source_tickets,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (body.id, body.category, body.title, body.content, json.dumps(body.tags), json.dumps(body.source_tickets), body.created_by, now, now))
    db().commit()
    return {"id": body.id}

# ── INBOX / ATTENTION / STATUS ───────────────────────────────

@app.get("/inbox/{agent_id}")
def inbox(agent_id: str):
    agent = db().execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not agent: raise HTTPException(404)
    assigned = [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM tickets WHERE assigned_to=?", (agent_id,)).fetchall()]
    # Available: tickets matching any of this agent's certified roles
    certs = db().execute("SELECT role_id FROM certifications WHERE agent_id=? AND status='passed'", (agent_id,)).fetchall()
    certified_roles = [c["role_id"] for c in certs]
    available = []
    for phase, role in PHASE_ROLE.items():
        if role in certified_roles:
            rows = db().execute("SELECT * FROM tickets WHERE phase=? AND blocked_by IS NULL AND assigned_to IS NULL ORDER BY priority DESC", (phase,)).fetchall()
            available.extend([_pj(row_to_dict(r)) for r in rows])
    return {"agent_id": agent_id, "certified_roles": certified_roles, "assigned": assigned, "available": available}

@app.get("/attention")
def attention():
    now = now_ms()
    review = [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM tickets WHERE phase IN ('preflight_review','design_review','code_review','merge_ready') AND assigned_to IS NULL ORDER BY priority DESC").fetchall()]
    expired = [row_to_dict(r) for r in db().execute("SELECT id,assigned_to,phase FROM tickets WHERE locked_at IS NOT NULL AND locked_at+lock_ttl_ms<?", (now,)).fetchall()]
    stuck = [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM tickets WHERE phase IN ('rework','preflight_rework') AND review_rounds>=3").fetchall()]
    pending_exams = [row_to_dict(r) for r in db().execute("SELECT agent_id,role_id,score FROM certifications WHERE status='pending_review'").fetchall()]
    monitoring = [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM tickets WHERE phase='monitoring'").fetchall()]
    return {"needs_review": review, "expired_locks": expired, "stuck_in_rework": stuck,
            "pending_exams": pending_exams, "monitoring": monitoring,
            "summary": {"review": len(review), "expired": len(expired), "stuck": len(stuck),
                        "exams": len(pending_exams), "monitoring": len(monitoring)}}

@app.get("/status")
def status():
    phases = db().execute("SELECT phase, COUNT(*) as c FROM tickets GROUP BY phase").fetchall()
    agents = db().execute("SELECT id,status,current_ticket,current_role FROM agents").fetchall()
    certs = db().execute("SELECT role_id, COUNT(*) as c FROM certifications WHERE status='passed' GROUP BY role_id").fetchall()
    return {"phases": {r["phase"]: r["c"] for r in phases}, "agents": [row_to_dict(a) for a in agents],
            "certified_per_role": {r["role_id"]: r["c"] for r in certs}, "total_tickets": sum(r["c"] for r in phases)}

@app.get("/events")
def events(ticket_id: str = None, agent_id: str = None, limit: int = 50):
    if ticket_id: rows = db().execute("SELECT * FROM event_log WHERE ticket_id=? ORDER BY timestamp DESC LIMIT ?", (ticket_id, limit)).fetchall()
    elif agent_id: rows = db().execute("SELECT * FROM event_log WHERE agent_id=? ORDER BY timestamp DESC LIMIT ?", (agent_id, limit)).fetchall()
    else: rows = db().execute("SELECT * FROM event_log ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    return {"events": [row_to_dict(r) for r in rows]}

# ── Gap 4: DORA Metrics ──────────────────────────────────────

@app.get("/metrics/dora")
def dora_metrics(window_days: int = 30):
    """Calculate DORA metrics from event log."""
    now = now_ms()
    rows = db().execute("SELECT * FROM event_log ORDER BY timestamp").fetchall()
    events_list = [row_to_dict(r) for r in rows]
    metrics = logic.calculate_dora(events_list, now, window_days)
    # Format for readability
    lead_time_hours = metrics.lead_time_ms / 3600000 if metrics.lead_time_ms else 0
    mttr_hours = metrics.mttr_ms / 3600000 if metrics.mttr_ms else 0
    return {
        "window_days": window_days,
        "deployment_frequency": f"{metrics.deployment_frequency:.3f} per day",
        "lead_time": f"{lead_time_hours:.1f} hours",
        "change_failure_rate": f"{metrics.change_failure_rate:.1%}",
        "mttr": f"{mttr_hours:.1f} hours",
        "raw": {
            "deployment_frequency": metrics.deployment_frequency,
            "lead_time_ms": metrics.lead_time_ms,
            "change_failure_rate": metrics.change_failure_rate,
            "mttr_ms": metrics.mttr_ms,
        }
    }

# ── Gap 2: Post-Mortem ───────────────────────────────────────

@app.get("/post-mortems")
def list_post_mortems():
    rows = db().execute("SELECT * FROM post_mortems ORDER BY created_at DESC").fetchall()
    return {"post_mortems": [_pj(row_to_dict(r)) for r in rows]}

@app.get("/post-mortems/{ticket_id}")
def get_post_mortem(ticket_id: str):
    """Analyze a ticket for post-mortem (can be called manually or auto-triggered)."""
    t = db().execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not t: raise HTTPException(404)
    # Gather all blocker comments
    comments = db().execute(
        "SELECT content FROM comments WHERE ticket_id=? AND comment_type='blocker'",
        (ticket_id,)).fetchall()
    blockers = [c["content"] for c in comments]
    result = logic.analyze_post_mortem(t["review_rounds"] or 0, blockers)
    return {
        "ticket_id": ticket_id,
        "review_rounds": t["review_rounds"],
        "should_trigger": result.should_trigger,
        "reason": result.reason,
        "patterns": result.patterns,
        "action_items": result.action_items
    }

# ── DEPLOY ────────────────────────────────────────────────────

@app.post("/projects/{pid}/deploy/{env}")
def deploy_to_env(pid: str, env: str, branch: str = "main"):
    """Deploy a project to pre or prod environment via SSH.

    Aegis SSHes into the target environment and runs deploy_command.
    Then checks health_check_url if configured.
    """
    if env not in ("pre", "prod"):
        raise HTTPException(400, "env must be 'pre' or 'prod'")

    proj = db().execute("SELECT repo_url,environments_json FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj: raise HTTPException(404)

    envs = json.loads(proj["environments_json"] or "{}")
    env_cfg = envs.get(env, {})
    if not env_cfg.get("ssh_host"):
        raise HTTPException(400, f"environments.{env}.ssh_host not configured")
    if not env_cfg.get("deploy_command"):
        raise HTTPException(400, f"environments.{env}.deploy_command not configured")

    from . import ci_runner
    now = now_ms()

    # Step 1: Deploy
    code, output = ci_runner._ssh_run(
        env_cfg["ssh_host"], env_cfg.get("ssh_user", "root"),
        env_cfg.get("ssh_port", 22), env_cfg.get("ssh_key_path", "~/.ssh/id_rsa"),
        env_cfg["deploy_command"],
        timeout=env_cfg.get("timeout_seconds", 300))

    if code != 0:
        log_event(db(), "deploy_failed", pid, "system", env, f"exit={code}")
        db().commit()
        raise HTTPException(500, {"message": f"Deploy to {env} failed",
                                   "output": output[:1000]})

    log_event(db(), "deployed", pid, "system", "", env)

    # Step 2: Health check
    health_ok = True
    health_output = ""
    if env_cfg.get("health_check_url"):
        h_code, h_output = ci_runner._ssh_run(
            env_cfg["ssh_host"], env_cfg.get("ssh_user", "root"),
            env_cfg.get("ssh_port", 22), env_cfg.get("ssh_key_path", "~/.ssh/id_rsa"),
            f"curl -sf {env_cfg['health_check_url']} || exit 1",
            timeout=30)
        health_ok = h_code == 0
        health_output = h_output

    db().commit()
    return {"project_id": pid, "env": env, "status": "ok" if health_ok else "unhealthy",
            "deploy_output": output[:500],
            "health_check": health_output[:200] if health_output else "not configured"}

# ── CANARY / MONITORING / ROLLBACK ────────────────────────────

@app.post("/tickets/{tid}/canary/check")
def canary_health_check(tid: str, body: MetricsReport):
    """Report metrics for a canary deployment. Aegis decides: promote, hold, or rollback."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    if t["phase"] != "monitoring":
        raise HTTPException(409, f"Ticket is in '{t['phase']}', not monitoring")

    stages = json.loads(t["canary_plan"] or "[]") or [25, 100]
    current_stage = t["canary_stage"] or stages[0]

    # Build MetricsSnapshot
    current = logic.MetricsSnapshot(
        error_rate=body.error_rate, latency_p50_ms=body.latency_p50_ms,
        latency_p99_ms=body.latency_p99_ms, request_rate=body.request_rate,
        saturation=body.saturation, timestamp_ms=now)

    # Evaluate health (no baseline for now — use thresholds only)
    health = logic.evaluate_health(current, baseline=None)

    # Record as evidence
    verdict = "pass" if health.ok else "fail"
    db().execute("INSERT INTO evidence (ticket_id,phase,agent_id,evidence_type,content,verdict,timestamp) VALUES(?,?,?,?,?,?,?)",
                 (tid, "monitoring", "system", "canary_health",
                  json.dumps({"stage": current_stage, "error_rate": body.error_rate,
                              "latency_p99": body.latency_p99_ms, "saturation": body.saturation}),
                  verdict, now))

    # Check rollback
    rollback_check = logic.should_auto_rollback(body.error_rate)
    if not rollback_check.ok:
        # Auto-create rollback ticket
        branch = t["branch"] or "main"
        plan = logic.create_rollback_plan(tid, branch, rollback_check.error)
        db().execute("INSERT INTO tickets (id,project_id,title,description,phase,priority,risk_level,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (plan.ticket_id, t["project_id"], f"ROLLBACK: {t['title']}",
                      plan.reason, "ready", 5, "critical", "system", now, now))
        db().execute("UPDATE tickets SET canary_stage=0,phase='rework',updated_at=? WHERE id=?", (now, tid))
        log_event(db(), "auto_rollback", tid, "system", "monitoring", "rollback")

        # Execute rollback on pre environment
        rollback_result = None
        if t["project_id"]:
            rollback_result = automation.execute_rollback(
                db(), t["project_id"], tid, "pre")
            # Send alert webhook if configured
            proj = db().execute("SELECT webhook_url FROM projects WHERE id=?", (t["project_id"],)).fetchone()
            alert = logic.build_alert(tid, t["project_id"], health, t["risk_level"])
            if alert and proj and proj["webhook_url"]:
                _send_webhook(proj["webhook_url"], alert)

        db().commit()
        return {"action": "rollback", "rollback_ticket": plan.ticket_id,
                "reason": rollback_check.error,
                "rollback_deploy": rollback_result}

    # Check promotion
    promote = logic.should_promote_canary(
        current_stage, stages, body.error_rate, body.latency_p99_ms)

    if promote.ok and promote.data.get("action") == "promote":
        next_stage = promote.data["to"]
        db().execute("UPDATE tickets SET canary_stage=?,updated_at=? WHERE id=?", (next_stage, now, tid))
        log_event(db(), "canary_promoted", tid, "system", str(current_stage), str(next_stage))

        # Auto-deploy to prod when canary reaches 100%
        deploy_result = None
        if next_stage >= 100:
            db().execute("UPDATE tickets SET phase='done',canary_stage=100,updated_at=? WHERE id=?", (now, tid))
            log_event(db(), "canary_complete", tid, "system", "monitoring", "done")
            # Auto-deploy to prod
            if t["project_id"]:
                deploy_result = _auto_deploy(t["project_id"], "prod")

        db().commit()
        return {"action": "promote", "from": current_stage, "to": next_stage,
                "health": "ok", "deploy": deploy_result}
    elif promote.ok and promote.data.get("action") == "complete":
        db().commit()
        return {"action": "complete", "stage": 100, "health": "ok"}

    db().commit()
    return {"action": "hold", "stage": current_stage, "health": verdict,
            "issues": health.data.get("issues", []) if health.data else []}


@app.get("/tickets/{tid}/canary")
def canary_status(tid: str):
    """Get current canary deployment status."""
    t = db().execute("SELECT id,phase,canary_stage,canary_plan,risk_level FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    stages = json.loads(t["canary_plan"] or "[]")
    return {"ticket_id": tid, "phase": t["phase"],
            "canary_stage": t["canary_stage"], "canary_plan": stages}


@app.post("/projects/{pid}/check-deps")
def check_project_deps(pid: str):
    """Check dependency pinning for a project via git clone."""
    proj = db().execute("SELECT repo_url,tech_stack FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj: raise HTTPException(404)

    from . import ci_runner
    work_dir, error = ci_runner.checkout_repo(proj["repo_url"])
    if error:
        return {"project_id": pid, "status": "error", "detail": error.detail}

    import os, shutil
    try:
        results = []
        # Check requirements.txt
        req_path = os.path.join(work_dir, "requirements.txt")
        if os.path.exists(req_path):
            content = open(req_path).read()
            r = logic.check_deps_manifest(content)
            results.append({"file": "requirements.txt", "ok": r.ok,
                           "detail": r.error if not r.ok else "All pinned"})

        # Check package.json (basic)
        pkg_path = os.path.join(work_dir, "package.json")
        if os.path.exists(pkg_path):
            results.append({"file": "package.json", "ok": True, "detail": "Found"})

        return {"project_id": pid, "status": "ok" if all(r["ok"] for r in results) else "warn",
                "checks": results}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/tickets/{tid}/check-owners")
def check_file_owners(tid: str, changed_files: list[str], reviewer_id: str):
    """Check if reviewer has file-level ownership for a review."""
    t = db().execute("SELECT project_id FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    if not t["project_id"]:
        return {"ok": True, "detail": "No project, no ownership rules"}

    proj = db().execute("SELECT conventions FROM projects WHERE id=?", (t["project_id"],)).fetchone()
    conventions = json.loads(proj["conventions"] or "{}") if proj else {}
    owners_map = conventions.get("owners_map", {})

    result = logic.check_file_ownership(changed_files, owners_map, reviewer_id)
    return {"ok": result.ok, "detail": result.error if not result.ok else "All files owned by reviewer",
            "data": result.data}


def _send_webhook(url: str, alert: logic.AlertPayload):
    """Send alert to webhook URL (fire-and-forget)."""
    import urllib.request
    payload = json.dumps({
        "severity": alert.severity,
        "title": alert.title,
        "description": alert.description,
        "ticket_id": alert.ticket_id,
        "project_id": alert.project_id,
        "metrics": alert.metrics
    }).encode()
    try:
        req = urllib.request.Request(url, data=payload,
                                      headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.warning(f"Webhook failed: {url} → {e}")


def _auto_deploy(project_id: str, env: str) -> dict | None:
    """Auto-deploy to pre or prod. Called by canary lifecycle.

    Returns deploy result dict or None if env not configured.
    """
    proj = db().execute("SELECT environments_json FROM projects WHERE id=?", (project_id,)).fetchone()
    if not proj: return None

    envs = json.loads(proj["environments_json"] or "{}")
    env_cfg = envs.get(env, {})
    if not env_cfg.get("ssh_host") or not env_cfg.get("deploy_command"):
        logger.info(f"Auto-deploy to {env} skipped — not configured for {project_id}")
        return {"status": "skipped", "reason": f"{env} not configured"}

    from . import ci_runner
    code, output = ci_runner._ssh_run(
        env_cfg["ssh_host"], env_cfg.get("ssh_user", "root"),
        env_cfg.get("ssh_port", 22), env_cfg.get("ssh_key_path", "~/.ssh/id_rsa"),
        env_cfg["deploy_command"],
        timeout=env_cfg.get("timeout_seconds", 300))

    log_event(db(), "auto_deployed" if code == 0 else "auto_deploy_failed",
              project_id, "system", "", env)

    # Health check
    health_ok = True
    if code == 0 and env_cfg.get("health_check_url"):
        h_code, _ = ci_runner._ssh_run(
            env_cfg["ssh_host"], env_cfg.get("ssh_user", "root"),
            env_cfg.get("ssh_port", 22), env_cfg.get("ssh_key_path", "~/.ssh/id_rsa"),
            f"curl -sf {env_cfg['health_check_url']} || exit 1", timeout=30)
        health_ok = h_code == 0

    return {"status": "ok" if code == 0 and health_ok else "failed",
            "env": env, "exit_code": code}


def main():
    import argparse; p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=9800)
    a = p.parse_args(); logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=a.host, port=a.port)


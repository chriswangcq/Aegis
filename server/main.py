"""Command Center server v0.4 — role-based team with certification exams."""
import json, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
import uvicorn
from .db import get_db, init_schema, seed_roles, now_ms, log_event, PHASE_ROLE, SUBMIT_NEXT, VALID_PHASES
from .models import *
from . import logic

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

def _trust(agent_id, role_id, ticket_id, dim, delta, reason):
    now = now_ms()
    db().execute("INSERT INTO trust_events (agent_id,role_id,ticket_id,dimension,delta,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                 (agent_id, role_id, ticket_id, dim, delta, reason, now))
    cert = db().execute("SELECT trust_json FROM certifications WHERE agent_id=? AND role_id=?", (agent_id, role_id)).fetchone()
    if cert:
        t = json.loads(cert["trust_json"]) if cert["trust_json"] else {}
        t[dim] = max(0.0, min(1.0, t.get(dim, 0.5) + delta))
        db().execute("UPDATE certifications SET trust_json=?, updated_at=? WHERE agent_id=? AND role_id=?",
                     (json.dumps(t), now, agent_id, role_id))

@asynccontextmanager
async def lifespan(app):
    global _conn
    _conn = get_db(); init_schema(_conn); seed_roles(_conn)
    logger.info("Command Center v0.4 ready"); yield; _conn.close()

app = FastAPI(title="NovAIC Command Center", version="0.4.0", lifespan=lifespan)

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
        certs = db().execute("SELECT role_id, status, score FROM certifications WHERE agent_id=? AND status='passed'", (a["id"],)).fetchall()
        d["certified_roles"] = [{"role": c["role_id"], "score": c["score"]} for c in certs]
        agents.append(d)
    return {"agents": agents}

@app.post("/agents")
def register_agent(body: AgentRegister):
    now = now_ms()
    db().execute("INSERT OR REPLACE INTO agents (id,display_name,provider,status,created_at,updated_at) VALUES(?,?,?,'idle',?,?)",
                 (body.id, body.display_name or body.id, body.provider, now, now))
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
def list_tickets(phase: str = None, available: bool = False):
    if available:
        phases = "','".join(PHASE_ROLE.keys())
        rows = db().execute(f"SELECT * FROM tickets WHERE phase IN ('{phases}') AND blocked_by IS NULL AND assigned_to IS NULL ORDER BY priority DESC, created_at ASC").fetchall()
    elif phase:
        rows = db().execute("SELECT * FROM tickets WHERE phase=? ORDER BY priority DESC, created_at ASC", (phase,)).fetchall()
    else:
        rows = db().execute("SELECT * FROM tickets ORDER BY priority DESC, created_at ASC").fetchall()
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
    # Store skip_preflight in scope_json
    if body.skip_preflight:
        scope["skip_preflight"] = True
    db().execute("INSERT INTO tickets (id,title,description,phase,depends_on,blocked_by,scope_json,checklist_json,priority,risk_level,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (body.id, body.title, body.description, phase, json.dumps(body.depends_on), blocked_by, json.dumps(scope), json.dumps(cl), body.priority, body.risk_level, body.created_by, now, now))
    log_event(db(), "ticket_created", body.id, new_value=phase); db().commit()
    return {"id": body.id, "phase": phase, "blocked_by": blocked_by, "skip_preflight": body.skip_preflight}

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

    # ── Logic: validate evidence ──
    ev_dicts = [{"evidence_type": ev.evidence_type, "content": ev.content, "verdict": ev.verdict} for ev in body.evidence]
    checklist = json.loads(t["checklist_json"]) if t["checklist_json"] else []
    ev_check = logic.validate_submit_evidence(phase, ev_dicts, checklist=checklist)
    if not ev_check.ok:
        raise HTTPException(400, ev_check.error)

    # ── System: automated gates ──
    gate_verdicts = logic.run_gates(phase, ev_dicts, checklist=checklist)
    failed_gates = [g for g in gate_verdicts if not g.passed]
    if failed_gates:
        details = [{"gate": g.gate, "reason": g.reason} for g in failed_gates]
        raise HTTPException(400, {
            "message": f"{len(failed_gates)} automated gate(s) failed",
            "failed_gates": details,
            "hint": "Fix these issues and re-submit. Gates are automated — no human can override them."
        })

    # ── I/O: write ──
    for ev in body.evidence:
        db().execute("INSERT INTO evidence (ticket_id,phase,agent_id,evidence_type,content,verdict,timestamp) VALUES(?,?,?,?,?,?,?)",
                     (tid, phase, body.agent_id, ev.evidence_type, ev.content, ev.verdict, now))
    # Record gate results as evidence too
    for g in gate_verdicts:
        db().execute("INSERT INTO evidence (ticket_id,phase,agent_id,evidence_type,content,verdict,timestamp) VALUES(?,?,?,?,?,?,?)",
                     (tid, phase, "system", "gate", f"[{g.gate}] {g.reason}", "pass" if g.passed else "fail", now))
    db().execute("UPDATE tickets SET phase=?,assigned_to=NULL,assigned_role=NULL,locked_at=NULL,updated_at=? WHERE id=?", (next_phase, now, tid))
    db().execute("UPDATE agents SET status='idle',current_ticket=NULL,current_role=NULL,updated_at=? WHERE id=?", (now, body.agent_id))
    role = t["assigned_role"] or "coder"
    _trust(body.agent_id, role, tid, "commit_discipline", +0.02, f"clean submit from {phase}")
    db().execute("UPDATE certifications SET tasks_completed=tasks_completed+1, updated_at=? WHERE agent_id=? AND role_id=?", (now, body.agent_id, role))
    log_event(db(), "submitted", tid, body.agent_id, phase, next_phase); db().commit()
    passed_gates = [g.gate for g in gate_verdicts if g.passed]
    return {"ticket_id": tid, "previous_phase": phase, "new_phase": next_phase,
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
    log_event(db(), "rejected", tid, rejector, t["phase"], next_phase, json.dumps({"reason": body.reason, "round": rounds})); db().commit()
    return {"ticket_id": tid, "phase": next_phase, "review_round": rounds, "rejected_by": rejector}

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
    review = [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM tickets WHERE phase IN ('preflight_review','code_review','merge_ready') AND assigned_to IS NULL ORDER BY priority DESC").fetchall()]
    expired = [row_to_dict(r) for r in db().execute("SELECT id,assigned_to,phase FROM tickets WHERE locked_at IS NOT NULL AND locked_at+lock_ttl_ms<?", (now,)).fetchall()]
    stuck = [_pj(row_to_dict(r)) for r in db().execute("SELECT * FROM tickets WHERE phase IN ('rework','preflight_rework') AND review_rounds>=3").fetchall()]
    pending_exams = [row_to_dict(r) for r in db().execute("SELECT agent_id,role_id,score FROM certifications WHERE status='pending_review'").fetchall()]
    return {"needs_review": review, "expired_locks": expired, "stuck_in_rework": stuck, "pending_exams": pending_exams,
            "summary": {"review": len(review), "expired": len(expired), "stuck": len(stuck), "exams": len(pending_exams)}}

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

def main():
    import argparse; p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=9800)
    a = p.parse_args(); logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=a.host, port=a.port)

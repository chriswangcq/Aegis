"""
novaic-command-center server — FastAPI + SQLite.
16-state ticket pipeline, comments, knowledge base, event log.
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
import uvicorn

from .db import (get_db, init_schema, now_ms, log_event,
                 CLAIMABLE, SUBMIT_NEXT, VALID_PHASES, PHASE_TIMEOUTS)
from .models import (TicketCreate, TicketClaim, TicketSubmit, TicketAdvance,
                     TicketReject, AgentRegister, CommentCreate, CommentUpdate,
                     KnowledgeCreate)

logger = logging.getLogger("command-center")
_conn = None


def db():
    return _conn


def row_to_dict(row) -> dict | None:
    return dict(row) if row else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _conn
    _conn = get_db()
    init_schema(_conn)
    logger.info("Command Center ready (16-state pipeline)")
    yield
    _conn.close()


app = FastAPI(title="NovAIC Command Center", version="0.2.0", lifespan=lifespan)


# ══════════════════════════════════════════════════════════════════════════════
#  TICKETS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/tickets")
def list_tickets(phase: str | None = None, available: bool = False):
    if available:
        phases = "','".join(CLAIMABLE.keys())
        rows = db().execute(
            f"SELECT * FROM tickets WHERE phase IN ('{phases}') AND blocked_by IS NULL "
            "ORDER BY priority DESC, created_at ASC"
        ).fetchall()
    elif phase:
        rows = db().execute(
            "SELECT * FROM tickets WHERE phase = ? ORDER BY priority DESC, created_at ASC", (phase,)
        ).fetchall()
    else:
        rows = db().execute("SELECT * FROM tickets ORDER BY priority DESC, created_at ASC").fetchall()
    return {"tickets": [row_to_dict(r) for r in rows], "count": len(rows)}


@app.get("/tickets/{tid}")
def get_ticket(tid: str):
    t = db().execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
    if not t:
        raise HTTPException(404)
    result = row_to_dict(t)
    result["evidence"] = [row_to_dict(e) for e in
        db().execute("SELECT * FROM evidence WHERE ticket_id=? ORDER BY timestamp", (tid,)).fetchall()]
    result["comments"] = [row_to_dict(c) for c in
        db().execute("SELECT * FROM comments WHERE ticket_id=? ORDER BY created_at", (tid,)).fetchall()]
    result["open_blockers"] = db().execute(
        "SELECT COUNT(*) as c FROM comments WHERE ticket_id=? AND comment_type='blocker' AND status='open'",
        (tid,)).fetchone()["c"]
    # Inject relevant knowledge
    result["knowledge"] = [row_to_dict(k) for k in
        db().execute("SELECT id,category,title FROM knowledge WHERE category='failure_pattern' "
                     "ORDER BY created_at DESC LIMIT 10").fetchall()]
    return result


@app.post("/tickets")
def create_ticket(body: TicketCreate):
    now = now_ms()
    checklist = [{"description": c, "status": "pending"} for c in body.checklist]
    scope = {}
    if body.scope_includes: scope["includes"] = body.scope_includes
    if body.scope_excludes: scope["excludes"] = body.scope_excludes

    blocked_by = None
    for dep in body.depends_on:
        row = db().execute("SELECT phase FROM tickets WHERE id=?", (dep,)).fetchone()
        if row and row["phase"] != "done":
            blocked_by = dep
            break

    phase = "ready" if not blocked_by else "planning"
    try:
        db().execute(
            """INSERT INTO tickets (id,title,description,phase,depends_on,blocked_by,
               scope_json,checklist_json,priority,risk_level,created_by,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (body.id, body.title, body.description, phase, json.dumps(body.depends_on),
             blocked_by, json.dumps(scope), json.dumps(checklist),
             body.priority, body.risk_level, body.created_by, now, now))
        log_event(db(), "ticket_created", body.id, new_value=phase)
        db().commit()
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"id": body.id, "phase": phase, "blocked_by": blocked_by}


@app.post("/tickets/{tid}/claim")
def claim_ticket(tid: str, body: TicketClaim):
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    if t["blocked_by"]: raise HTTPException(409, f"Blocked by {t['blocked_by']}")

    phase = t["phase"]
    is_expired = t["locked_at"] and t["lock_ttl_ms"] and (now - t["locked_at"] > t["lock_ttl_ms"])

    if phase not in CLAIMABLE and not is_expired:
        raise HTTPException(409, f"Phase '{phase}' not claimable")

    role = CLAIMABLE.get(phase, "coder")
    # After claim, phase stays the same for review/qa (they work in that phase)
    # For ready → preflight (coder starts preflight)
    next_phase_map = {
        "ready": "preflight", "preflight_rework": "preflight_rework",
        "rework": "rework", "preflight_review": "preflight_review",
        "code_review": "code_review", "qa": "qa", "deploy_prep": "deploy_prep",
    }
    next_phase = next_phase_map.get(phase, phase)

    result = db().execute(
        """UPDATE tickets SET phase=?, assigned_to=?, assigned_role=?, locked_at=?, updated_at=?
           WHERE id=? AND (phase=? OR (locked_at IS NOT NULL AND locked_at+lock_ttl_ms<?))""",
        (next_phase, body.agent_id, role, now, now, tid, phase, now))
    if result.rowcount == 0:
        raise HTTPException(409, "Race: claimed by someone else")

    db().execute("UPDATE agents SET status='busy', current_ticket=?, last_active_at=?, updated_at=? WHERE id=?",
                 (tid, now, now, body.agent_id))
    log_event(db(), "claimed", tid, body.agent_id, phase, next_phase)
    db().commit()
    return {"ticket_id": tid, "role": role, "phase": next_phase, "agent_id": body.agent_id}


@app.post("/tickets/{tid}/submit")
def submit_ticket(tid: str, body: TicketSubmit):
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    if t["assigned_to"] != body.agent_id:
        raise HTTPException(403, f"Assigned to {t['assigned_to']}")

    phase = t["phase"]
    next_phase = SUBMIT_NEXT.get(phase)
    if not next_phase:
        raise HTTPException(409, f"Cannot submit from '{phase}'")

    # Gate: check open blockers
    blockers = db().execute(
        "SELECT COUNT(*) as c FROM comments WHERE ticket_id=? AND comment_type='blocker' AND status='open'",
        (tid,)).fetchone()["c"]
    if blockers > 0:
        raise HTTPException(409, f"{blockers} unresolved blocker comment(s)")

    for ev in body.evidence:
        db().execute("INSERT INTO evidence (ticket_id,phase,agent_id,evidence_type,content,verdict,timestamp) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (tid, phase, body.agent_id, ev.evidence_type, ev.content, ev.verdict, now))

    db().execute("UPDATE tickets SET phase=?, assigned_to=NULL, assigned_role=NULL, locked_at=NULL, updated_at=? WHERE id=?",
                 (next_phase, now, tid))
    db().execute("UPDATE agents SET status='idle', current_ticket=NULL, success_count=success_count+1, updated_at=? WHERE id=?",
                 (now, body.agent_id))
    log_event(db(), "submitted", tid, body.agent_id, phase, next_phase)
    db().commit()
    return {"ticket_id": tid, "previous_phase": phase, "new_phase": next_phase}


@app.post("/tickets/{tid}/reject")
def reject_ticket(tid: str, body: TicketReject):
    """Master/CR rejects ticket back to rework phase."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)

    phase = t["phase"]
    reject_map = {
        "preflight_review": "preflight_rework",
        "code_review": "rework",
        "qa": "rework",
        "merge_ready": "rework",
    }
    next_phase = reject_map.get(phase)
    if not next_phase:
        raise HTTPException(409, f"Cannot reject from '{phase}'")

    # Auto-create blocker comments
    for bc in body.blocker_comments:
        db().execute(
            "INSERT INTO comments (ticket_id,author_id,author_role,content,comment_type,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (tid, t["assigned_to"] or "master", "master", bc, "blocker", "open", now, now))

    rounds = (t["review_rounds"] or 0) + 1
    db().execute(
        "UPDATE tickets SET phase=?, assigned_to=NULL, assigned_role=NULL, locked_at=NULL, review_rounds=?, updated_at=? WHERE id=?",
        (next_phase, rounds, now, tid))
    if t["assigned_to"]:
        db().execute("UPDATE agents SET status='idle', current_ticket=NULL, failure_count=failure_count+1, updated_at=? WHERE id=?",
                     (now, t["assigned_to"]))
    log_event(db(), "rejected", tid, t["assigned_to"], phase, next_phase,
              json.dumps({"reason": body.reason, "round": rounds}))
    db().commit()
    return {"ticket_id": tid, "phase": next_phase, "review_round": rounds, "blockers_added": len(body.blocker_comments)}


@app.post("/tickets/{tid}/advance")
def advance_ticket(tid: str, body: TicketAdvance):
    """Master manually advances phase."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    if body.target_phase not in VALID_PHASES:
        raise HTTPException(400, f"Invalid phase: {body.target_phase}")

    old_phase = t["phase"]
    db().execute("UPDATE tickets SET phase=?, assigned_to=NULL, locked_at=NULL, updated_at=? WHERE id=?",
                 (body.target_phase, now, tid))

    unblocked = []
    if body.target_phase == "done":
        deps = db().execute("SELECT id FROM tickets WHERE blocked_by=?", (tid,)).fetchall()
        for dep in deps:
            db().execute("UPDATE tickets SET blocked_by=NULL, phase='ready', updated_at=? WHERE id=?",
                         (now, dep["id"]))
            unblocked.append(dep["id"])
            log_event(db(), "unblocked", dep["id"], new_value="ready")

    log_event(db(), "advanced", tid, old_value=old_phase, new_value=body.target_phase,
              metadata=json.dumps({"reason": body.reason}))
    db().commit()
    result = {"ticket_id": tid, "phase": body.target_phase}
    if unblocked: result["unblocked"] = unblocked
    return result


@app.post("/tickets/{tid}/release")
def release_ticket(tid: str, body: TicketClaim):
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    revert = {"preflight": "ready", "preflight_rework": "ready", "rework": "code_review",
              "preflight_review": "preflight_review", "code_review": "code_review",
              "qa": "qa", "deploy_prep": "deploy_prep"}
    prev = revert.get(t["phase"], "ready")
    db().execute("UPDATE tickets SET phase=?, assigned_to=NULL, assigned_role=NULL, locked_at=NULL, updated_at=? WHERE id=?",
                 (prev, now, tid))
    db().execute("UPDATE agents SET status='idle', current_ticket=NULL, updated_at=? WHERE id=?",
                 (now, body.agent_id))
    log_event(db(), "released", tid, body.agent_id, t["phase"], prev)
    db().commit()
    return {"ticket_id": tid, "phase": prev}


# ══════════════════════════════════════════════════════════════════════════════
#  COMMENTS (Discussion per ticket)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/tickets/{tid}/comments")
def list_comments(tid: str):
    rows = db().execute("SELECT * FROM comments WHERE ticket_id=? ORDER BY created_at", (tid,)).fetchall()
    return {"comments": [row_to_dict(r) for r in rows], "count": len(rows)}


@app.post("/tickets/{tid}/comments")
def create_comment(tid: str, body: CommentCreate):
    now = now_ms()
    t = db().execute("SELECT id FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t: raise HTTPException(404)
    cur = db().execute(
        "INSERT INTO comments (ticket_id,author_id,author_role,content,comment_type,status,refs,parent_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, body.author_id, body.author_role, body.content, body.comment_type,
         "open", json.dumps(body.refs), body.parent_id, now, now))
    log_event(db(), "comment_added", tid, body.author_id, new_value=body.comment_type)
    db().commit()
    return {"id": cur.lastrowid, "ticket_id": tid, "comment_type": body.comment_type}


@app.patch("/tickets/{tid}/comments/{cid}")
def update_comment_status(tid: str, cid: int, body: CommentUpdate):
    now = now_ms()
    db().execute("UPDATE comments SET status=?, updated_at=? WHERE id=? AND ticket_id=?",
                 (body.status, now, cid, tid))
    log_event(db(), "comment_resolved", tid, new_value=body.status)
    db().commit()
    return {"id": cid, "status": body.status}


# ══════════════════════════════════════════════════════════════════════════════
#  KNOWLEDGE BASE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/knowledge")
def list_knowledge(category: str | None = None, q: str | None = None):
    if q:
        rows = db().execute(
            "SELECT * FROM knowledge WHERE title LIKE ? OR content LIKE ? ORDER BY created_at DESC",
            (f"%{q}%", f"%{q}%")).fetchall()
    elif category:
        rows = db().execute("SELECT * FROM knowledge WHERE category=? ORDER BY created_at DESC",
                            (category,)).fetchall()
    else:
        rows = db().execute("SELECT * FROM knowledge ORDER BY category, created_at DESC").fetchall()
    return {"items": [row_to_dict(r) for r in rows], "count": len(rows)}


@app.get("/knowledge/{kid}")
def get_knowledge(kid: str):
    k = db().execute("SELECT * FROM knowledge WHERE id=?", (kid,)).fetchone()
    if not k: raise HTTPException(404)
    return row_to_dict(k)


@app.post("/knowledge")
def create_knowledge(body: KnowledgeCreate):
    now = now_ms()
    db().execute(
        "INSERT OR REPLACE INTO knowledge (id,category,title,content,tags,source_tickets,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (body.id, body.category, body.title, body.content, json.dumps(body.tags),
         json.dumps(body.source_tickets), body.created_by, now, now))
    log_event(db(), "knowledge_created", new_value=body.id)
    db().commit()
    return {"id": body.id, "category": body.category}


# ══════════════════════════════════════════════════════════════════════════════
#  AGENTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/agents")
def list_agents():
    return {"agents": [row_to_dict(r) for r in db().execute("SELECT * FROM agents ORDER BY role,id").fetchall()]}


@app.post("/agents")
def register_agent(body: AgentRegister):
    now = now_ms()
    trust = {"code_quality": 0.5, "test_quality": 0.5, "commit_discipline": 0.5, "review_thoroughness": 0.5}
    db().execute(
        "INSERT OR REPLACE INTO agents (id,display_name,role,provider,status,trust_json,capabilities,created_at,updated_at) "
        "VALUES (?,?,?,?,'idle',?,?,?,?)",
        (body.id, body.display_name or body.id, body.role, body.provider,
         json.dumps(trust), json.dumps(body.capabilities), now, now))
    log_event(db(), "agent_registered", agent_id=body.id, new_value=body.role)
    db().commit()
    return {"id": body.id, "role": body.role}


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    a = db().execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not a: raise HTTPException(404)
    result = row_to_dict(a)
    result["recent_events"] = [row_to_dict(e) for e in
        db().execute("SELECT * FROM trust_events WHERE agent_id=? ORDER BY created_at DESC LIMIT 20",
                     (agent_id,)).fetchall()]
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  STATUS + EVENT LOG
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/status")
def status_dashboard():
    now = now_ms()
    phases = db().execute("SELECT phase, COUNT(*) as c FROM tickets GROUP BY phase").fetchall()
    blocked = db().execute("SELECT id, blocked_by FROM tickets WHERE blocked_by IS NOT NULL").fetchall()
    expired = db().execute(
        "SELECT id, assigned_to, phase FROM tickets WHERE locked_at IS NOT NULL AND locked_at+lock_ttl_ms<?",
        (now,)).fetchall()
    agents = db().execute("SELECT id,role,status,current_ticket FROM agents").fetchall()
    open_blockers = db().execute(
        "SELECT ticket_id, COUNT(*) as c FROM comments WHERE comment_type='blocker' AND status='open' GROUP BY ticket_id"
    ).fetchall()
    return {
        "phases": {r["phase"]: r["c"] for r in phases},
        "blocked": [{"id": b["id"], "by": b["blocked_by"]} for b in blocked],
        "expired_locks": [row_to_dict(e) for e in expired],
        "agents": [row_to_dict(a) for a in agents],
        "open_blockers": {r["ticket_id"]: r["c"] for r in open_blockers},
        "total_tickets": sum(r["c"] for r in phases),
    }


@app.get("/events")
def list_events(ticket_id: str | None = None, agent_id: str | None = None, limit: int = 50):
    if ticket_id:
        rows = db().execute("SELECT * FROM event_log WHERE ticket_id=? ORDER BY timestamp DESC LIMIT ?",
                            (ticket_id, limit)).fetchall()
    elif agent_id:
        rows = db().execute("SELECT * FROM event_log WHERE agent_id=? ORDER BY timestamp DESC LIMIT ?",
                            (agent_id, limit)).fetchall()
    else:
        rows = db().execute("SELECT * FROM event_log ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    return {"events": [row_to_dict(r) for r in rows]}


# ══════════════════════════════════════════════════════════════════════════════
#  FAILURE PATTERNS (convenience alias over knowledge)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/failure-patterns")
def list_failure_patterns():
    rows = db().execute("SELECT * FROM knowledge WHERE category='failure_pattern' ORDER BY created_at DESC").fetchall()
    return {"patterns": [row_to_dict(r) for r in rows]}


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9800)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    uvicorn.run(app, host=args.host, port=args.port)

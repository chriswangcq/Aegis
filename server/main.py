"""
novaic-command-center server — FastAPI + SQLite.

Usage:
    python -m server.main                    # default :9800
    python -m server.main --port 9801        # custom port
    python -m server.main --db ./my.db       # custom db path
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
import uvicorn

from .db import get_db, init_schema, now_ms
from .models import TicketCreate, TicketClaim, TicketSubmit, TicketAdvance, AgentRegister

logger = logging.getLogger("command-center")

_conn = None


def db():
    return _conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _conn
    _conn = get_db()
    init_schema(_conn)
    logger.info("Command Center DB initialized")
    yield
    _conn.close()


app = FastAPI(title="NovAIC Command Center", version="0.1.0", lifespan=lifespan)


def row_to_dict(row) -> dict:
    if row is None:
        return None
    return dict(row)


# ── Tickets ──────────────────────────────────────────────────────────────────

@app.get("/tickets")
def list_tickets(
    phase: Optional[str] = None,
    available: bool = False,
):
    """Browse tickets. Workers call this to find work."""
    if available:
        rows = db().execute(
            "SELECT * FROM tickets WHERE phase = 'ready' AND blocked_by IS NULL "
            "ORDER BY priority DESC, created_at ASC"
        ).fetchall()
    elif phase:
        rows = db().execute(
            "SELECT * FROM tickets WHERE phase = ? ORDER BY priority DESC, created_at ASC",
            (phase,)
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM tickets ORDER BY priority DESC, created_at ASC"
        ).fetchall()
    return {"tickets": [row_to_dict(r) for r in rows], "count": len(rows)}


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    """Read ticket details. Worker reads this before deciding to claim."""
    t = db().execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not t:
        raise HTTPException(404, f"Ticket {ticket_id} not found")

    result = row_to_dict(t)

    # Attach evidence
    evidence = db().execute(
        "SELECT * FROM evidence WHERE ticket_id = ? ORDER BY timestamp ASC", (ticket_id,)
    ).fetchall()
    result["evidence"] = [row_to_dict(e) for e in evidence]

    # Attach relevant failure patterns
    patterns = db().execute(
        "SELECT * FROM failure_patterns WHERE severity IN ('critical','high')"
    ).fetchall()
    result["failure_patterns"] = [row_to_dict(p) for p in patterns]

    return result


@app.post("/tickets")
def create_ticket(body: TicketCreate):
    """Master creates a new ticket."""
    now = now_ms()
    checklist = [{"description": c, "status": "pending"} for c in body.checklist]
    scope = {}
    if body.scope_includes:
        scope["includes"] = body.scope_includes
    if body.scope_excludes:
        scope["excludes"] = body.scope_excludes

    # Auto-block if deps aren't done
    blocked_by = None
    for dep in body.depends_on:
        row = db().execute("SELECT phase FROM tickets WHERE id = ?", (dep,)).fetchone()
        if row and row["phase"] != "done":
            blocked_by = dep
            break

    phase = "ready" if not blocked_by else "planning"

    try:
        db().execute(
            """INSERT INTO tickets (id, title, description, phase, depends_on, blocked_by,
               scope_json, checklist_json, priority, risk_level, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.id, body.title, body.description, phase,
             json.dumps(body.depends_on), blocked_by,
             json.dumps(scope), json.dumps(checklist),
             body.priority, body.risk_level, body.created_by, now, now)
        )
        db().commit()
    except Exception as e:
        raise HTTPException(400, str(e))

    return {"id": body.id, "phase": phase, "blocked_by": blocked_by}


@app.post("/tickets/{ticket_id}/claim")
def claim_ticket(ticket_id: str, body: TicketClaim):
    """Worker atomically claims a ticket."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not t:
        raise HTTPException(404, f"Ticket {ticket_id} not found")

    if t["blocked_by"]:
        raise HTTPException(409, f"Blocked by {t['blocked_by']}")

    claimable_phases = ("ready", "review_pending", "qa_pending", "changes_requested")
    is_expired = (t["locked_at"] and t["lock_ttl_ms"] and
                  now - t["locked_at"] > t["lock_ttl_ms"])

    if t["phase"] not in claimable_phases and not is_expired:
        raise HTTPException(409, f"Phase '{t['phase']}' is not claimable")

    role_map = {"ready": "coder", "changes_requested": "coder",
                "review_pending": "cr", "qa_pending": "qa"}
    role = role_map.get(t["phase"], "coder")
    next_phase = f"claimed:{role}"

    # Atomic CAS
    result = db().execute(
        """UPDATE tickets SET phase = ?, assigned_to = ?, assigned_role = ?,
           locked_at = ?, updated_at = ?
           WHERE id = ? AND (phase IN (?, 'review_pending', 'qa_pending', 'changes_requested')
                             OR (locked_at + lock_ttl_ms < ?))""",
        (next_phase, body.agent_id, role, now, now,
         ticket_id, t["phase"], now)
    )
    db().commit()

    if result.rowcount == 0:
        raise HTTPException(409, "Race condition: claimed by someone else")

    # Update agent
    db().execute(
        """UPDATE agents SET status = 'busy', current_ticket = ?,
           last_active_at = ?, updated_at = ? WHERE id = ?""",
        (ticket_id, now, now, body.agent_id)
    )
    db().commit()

    return {"ticket_id": ticket_id, "role": role, "phase": next_phase, "agent_id": body.agent_id}


@app.post("/tickets/{ticket_id}/submit")
def submit_ticket(ticket_id: str, body: TicketSubmit):
    """Worker submits completed work. Records evidence and advances phase."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not t:
        raise HTTPException(404)

    if t["assigned_to"] != body.agent_id:
        raise HTTPException(403, f"Not your ticket. Assigned to {t['assigned_to']}")

    phase_map = {
        "claimed:coder": "review_pending",
        "claimed:cr": "qa_pending",
        "claimed:qa": "merge_pending",
    }
    next_phase = phase_map.get(t["phase"])
    if not next_phase:
        raise HTTPException(409, f"Cannot submit from phase '{t['phase']}'")

    # Store evidence
    for ev in body.evidence:
        db().execute(
            """INSERT INTO evidence (ticket_id, phase, agent_id, evidence_type, content, verdict, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticket_id, t["phase"], body.agent_id, ev.evidence_type, ev.content, ev.verdict, now)
        )

    # Advance phase
    db().execute(
        """UPDATE tickets SET phase = ?, assigned_to = NULL, assigned_role = NULL,
           locked_at = NULL, updated_at = ? WHERE id = ?""",
        (next_phase, now, ticket_id)
    )
    db().execute(
        "UPDATE agents SET status = 'idle', current_ticket = NULL, updated_at = ? WHERE id = ?",
        (now, body.agent_id)
    )

    # Update agent success count
    db().execute(
        "UPDATE agents SET success_count = success_count + 1 WHERE id = ?",
        (body.agent_id,)
    )
    db().commit()

    return {"ticket_id": ticket_id, "previous_phase": t["phase"], "new_phase": next_phase}


@app.post("/tickets/{ticket_id}/release")
def release_ticket(ticket_id: str, body: TicketClaim):
    """Worker gives up a claimed ticket."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not t:
        raise HTTPException(404)

    revert = {"claimed:coder": "ready", "claimed:cr": "review_pending", "claimed:qa": "qa_pending"}
    prev_phase = revert.get(t["phase"], "ready")

    db().execute(
        """UPDATE tickets SET phase = ?, assigned_to = NULL, assigned_role = NULL,
           locked_at = NULL, updated_at = ? WHERE id = ?""",
        (prev_phase, now, ticket_id)
    )
    db().execute(
        "UPDATE agents SET status = 'idle', current_ticket = NULL, updated_at = ? WHERE id = ?",
        (now, body.agent_id)
    )
    db().commit()
    return {"ticket_id": ticket_id, "phase": prev_phase}


@app.post("/tickets/{ticket_id}/advance")
def advance_ticket(ticket_id: str, body: TicketAdvance):
    """Master manually advances phase (merge, canary, done)."""
    now = now_ms()
    t = db().execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not t:
        raise HTTPException(404)

    db().execute(
        "UPDATE tickets SET phase = ?, updated_at = ? WHERE id = ?",
        (body.target_phase, now, ticket_id)
    )

    # If ticket is done, unblock dependents
    if body.target_phase == "done":
        dependents = db().execute(
            "SELECT id FROM tickets WHERE blocked_by = ?", (ticket_id,)
        ).fetchall()
        for dep in dependents:
            db().execute(
                "UPDATE tickets SET blocked_by = NULL, phase = 'ready', updated_at = ? WHERE id = ?",
                (now, dep["id"])
            )

    db().commit()

    result = {"ticket_id": ticket_id, "phase": body.target_phase}
    if body.target_phase == "done":
        unblocked = [d["id"] for d in db().execute(
            "SELECT id FROM tickets WHERE phase = 'ready' AND updated_at = ?", (now,)
        ).fetchall()]
        if unblocked:
            result["unblocked"] = unblocked
    return result


# ── Agents ───────────────────────────────────────────────────────────────────

@app.get("/agents")
def list_agents():
    rows = db().execute("SELECT * FROM agents ORDER BY role, id").fetchall()
    return {"agents": [row_to_dict(r) for r in rows]}


@app.post("/agents")
def register_agent(body: AgentRegister):
    now = now_ms()
    trust = {"code_quality": 0.5, "test_quality": 0.5,
             "commit_discipline": 0.5, "review_thoroughness": 0.5}
    db().execute(
        """INSERT OR REPLACE INTO agents
           (id, display_name, role, provider, status, trust_json, capabilities, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'idle', ?, ?, ?, ?)""",
        (body.id, body.display_name or body.id, body.role, body.provider,
         json.dumps(trust), json.dumps(body.capabilities), now, now)
    )
    db().commit()
    return {"id": body.id, "role": body.role}


# ── Status Dashboard ─────────────────────────────────────────────────────────

@app.get("/status")
def status_dashboard():
    now = now_ms()
    phases = db().execute("SELECT phase, COUNT(*) as c FROM tickets GROUP BY phase").fetchall()
    blocked = db().execute(
        "SELECT id, blocked_by FROM tickets WHERE blocked_by IS NOT NULL"
    ).fetchall()
    expired = db().execute(
        "SELECT id, assigned_to FROM tickets WHERE locked_at IS NOT NULL AND locked_at + lock_ttl_ms < ?",
        (now,)
    ).fetchall()
    agents = db().execute("SELECT id, role, status, current_ticket FROM agents").fetchall()

    return {
        "phases": {r["phase"]: r["c"] for r in phases},
        "blocked": [{"id": b["id"], "by": b["blocked_by"]} for b in blocked],
        "expired_locks": [row_to_dict(e) for e in expired],
        "agents": [row_to_dict(a) for a in agents],
        "total_tickets": sum(r["c"] for r in phases),
    }


# ── Failure Patterns ─────────────────────────────────────────────────────────

@app.get("/failure-patterns")
def list_failure_patterns():
    rows = db().execute("SELECT * FROM failure_patterns ORDER BY severity DESC").fetchall()
    return {"patterns": [row_to_dict(r) for r in rows]}


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9800)
    parser.add_argument("--db", type=str, default=None)
    args = parser.parse_args()

    if args.db:
        from . import db as db_module
        db_module.DEFAULT_DB = Path(args.db)

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

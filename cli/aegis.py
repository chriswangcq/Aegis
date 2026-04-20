#!/usr/bin/env python3
"""Aegis CLI — connect any agent (human via Claude Code, AI, CI) to the Aegis governance platform.

Usage:
    # First time: configure project
    aegis init --server http://aegis.internal:9800 --project my-app --api-key aegis_my-app_agent_xxx

    # Register and start working
    aegis register --id chris-claude --provider gemini --webhook http://localhost:3000/hook

    # Work on tickets
    aegis tickets                      # list available tickets
    aegis claim PR-42                  # claim a ticket
    aegis submit PR-42 --branch feat   # submit implementation (triggers SSH CI)
    aegis submit PR-42 --verdict pass  # submit review
    aegis advance PR-42 --to done      # master: advance phase

    # Deploy
    aegis deploy pre                   # deploy to pre environment
    aegis deploy prod                  # deploy to prod

    # Status
    aegis status                       # server health
    aegis whoami                       # current agent info
    aegis project                      # project dashboard

Works with:
    - Claude Code (human types aegis commands in terminal)
    - Cursor / Gemini / any AI that can run shell commands
    - CI/CD systems
    - Direct HTTP calls
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import request, error

CONFIG_DIR = Path.home() / ".aegis"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def _save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _api(method: str, path: str, body: dict = None, cfg: dict = None) -> dict:
    """Call Aegis API."""
    if cfg is None:
        cfg = _load_config()
    server = cfg.get("server", "http://localhost:9800")
    url = f"{server.rstrip('/')}{path}"

    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    api_key = cfg.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body)
            if isinstance(detail, dict):
                msg = detail.get("detail", detail)
            else:
                msg = detail
        except json.JSONDecodeError:
            msg = body
        print(f"❌ {e.code}: {msg}", file=sys.stderr)
        sys.exit(1)
    except error.URLError as e:
        print(f"❌ Cannot connect to Aegis: {e.reason}", file=sys.stderr)
        print(f"   Server: {server}", file=sys.stderr)
        print(f"   Run: aegis init --server <url>", file=sys.stderr)
        sys.exit(1)


def _pp(data):
    """Pretty print JSON."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ── Commands ─────────────────────────────────────────────────

def cmd_init(args):
    """Configure Aegis CLI for a project."""
    cfg = _load_config()
    if args.server:
        cfg["server"] = args.server
    if args.project:
        cfg["project"] = args.project
    if args.api_key:
        cfg["api_key"] = args.api_key
    if args.agent_id:
        cfg["agent_id"] = args.agent_id
    _save_config(cfg)
    print(f"✅ Aegis configured")
    print(f"   Server:  {cfg.get('server', 'http://localhost:9800')}")
    print(f"   Project: {cfg.get('project', '(not set)')}")
    print(f"   Agent:   {cfg.get('agent_id', '(not set)')}")


def cmd_status(args):
    """Check Aegis server health."""
    data = _api("GET", "/status")
    print(f"🟢 Aegis v{data['version']}")
    print(f"   Projects: {data['projects']}  Tickets: {data['tickets']}  Agents: {data['agents']}")


def cmd_register(args):
    """Register as an agent."""
    cfg = _load_config()
    agent_id = args.id or cfg.get("agent_id")
    if not agent_id:
        print("❌ --id required (or set via aegis init --agent-id)", file=sys.stderr)
        sys.exit(1)
    body = {"id": agent_id, "provider": args.provider or "unknown"}
    if args.webhook:
        body["webhook_url"] = args.webhook
    data = _api("POST", "/agents", body)
    # Only update local config if --id was explicitly provided
    if args.id:
        cfg["agent_id"] = agent_id
        _save_config(cfg)
    print(f"✅ Registered as '{agent_id}'")
    print(f"   Next: {data.get('next_step', '')}")



def cmd_whoami(args):
    """Show current agent info."""
    cfg = _load_config()
    agent_id = cfg.get("agent_id")
    if not agent_id:
        print("❌ Not configured. Run: aegis init --agent-id <id>", file=sys.stderr)
        sys.exit(1)
    data = _api("GET", f"/agents/{agent_id}")
    print(f"🤖 {data['id']} ({data.get('provider', '?')})")
    print(f"   Status: {data.get('status', '?')}")
    if data.get("current_ticket"):
        print(f"   Working on: {data['current_ticket']} as {data.get('current_role', '?')}")
    print(f"   Ready to claim tickets.")



def cmd_tickets(args):
    """List tickets."""
    cfg = _load_config()
    project = args.project or cfg.get("project", "")
    params = []
    if project:
        params.append(f"project_id={project}")
    if args.phase:
        params.append(f"phase={args.phase}")
    qs = f"?{'&'.join(params)}" if params else ""
    data = _api("GET", f"/tickets{qs}")
    tickets = data.get("tickets", [])
    if not tickets:
        print("📭 No tickets found")
        return
    print(f"📋 {len(tickets)} ticket(s):\n")
    for t in tickets:
        icon = {"ready": "🟢", "implementation": "🔨", "code_review": "👀",
                "monitoring": "📊", "done": "✅", "rework": "🔄"}.get(t.get("phase", ""), "⬜")
        assigned = f" → {t['assigned_to']}" if t.get("assigned_to") else ""
        print(f"  {icon} {t['id']:20s} {t.get('phase', '?'):18s} P{t.get('priority', 0)} {t.get('title', '')[:40]}{assigned}")


def cmd_claim(args):
    """Claim a ticket."""
    cfg = _load_config()
    agent_id = cfg.get("agent_id")
    if not agent_id:
        print("❌ Run: aegis init --agent-id <id>", file=sys.stderr)
        sys.exit(1)
    data = _api("POST", f"/tickets/{args.ticket_id}/claim", {"agent_id": agent_id})
    print(f"✅ Claimed {args.ticket_id}")
    print(f"   Role: {data.get('role', '?')}")
    print(f"   Phase: {data.get('phase', '?')}")


def cmd_submit(args):
    """Submit work for a ticket."""
    cfg = _load_config()
    agent_id = cfg.get("agent_id")
    if not agent_id:
        print("❌ Run: aegis init --agent-id <id>", file=sys.stderr)
        sys.exit(1)
    body = {"agent_id": agent_id}
    if args.branch:
        body["branch"] = args.branch
    if args.commit:
        body["commit_sha"] = args.commit
    if args.verdict:
        body["evidence"] = [{"evidence_type": "review", "content": args.message or "reviewed",
                              "verdict": args.verdict}]
    elif args.evidence_type:
        body["evidence"] = [{"evidence_type": args.evidence_type,
                              "content": args.message or "", "verdict": args.verdict or "pass"}]
    data = _api("POST", f"/tickets/{args.ticket_id}/submit", body)
    print(f"✅ Submitted {args.ticket_id}")
    print(f"   Phase: {data.get('new_phase', data.get('phase', '?'))}")
    if data.get("gates_passed"):
        print(f"   CI: {len(data['gates_passed'])} gate(s) passed")
    if data.get("verification_mode"):
        print(f"   Verification: {data['verification_mode']}")


def cmd_advance(args):
    """Advance a ticket to next phase (master only)."""
    body = {"target_phase": args.to, "reason": args.reason or ""}
    cfg = _load_config()
    if cfg.get("agent_id"):
        body["agent_id"] = cfg["agent_id"]
    data = _api("POST", f"/tickets/{args.ticket_id}/advance", body)
    phase = data.get("phase", data.get("new_phase", "?"))
    print(f"✅ Advanced {args.ticket_id} → {phase}")
    if data.get("deploy"):
        d = data["deploy"]
        print(f"   Deploy: {d.get('status', '?')} ({d.get('env', '?')})")
    if data.get("unblocked"):
        print(f"   Unblocked: {', '.join(data['unblocked'])}")


def cmd_reject(args):
    """Reject a ticket back to rework."""
    cfg = _load_config()
    body = {"reason": args.reason}
    if cfg.get("agent_id"):
        body["agent_id"] = cfg["agent_id"]
    body["blocker_comments"] = args.blockers or [args.reason]
    data = _api("POST", f"/tickets/{args.ticket_id}/reject", body)
    print(f"🔄 Rejected {args.ticket_id}")
    print(f"   Phase: {data.get('phase', '?')}")
    if data.get("review_round"):
        print(f"   Round: {data['review_round']}")


def cmd_deploy(args):
    """Deploy to pre or prod."""
    cfg = _load_config()
    project = cfg.get("project")
    if not project:
        print("❌ Run: aegis init --project <id>", file=sys.stderr)
        sys.exit(1)
    data = _api("POST", f"/projects/{project}/deploy/{args.env}")
    status_icon = "✅" if data.get("status") == "ok" else "❌"
    print(f"{status_icon} Deploy to {args.env}: {data.get('status', '?')}")
    if data.get("health_check"):
        print(f"   Health: {data['health_check'][:100]}")


def cmd_project(args):
    """Show project dashboard."""
    cfg = _load_config()
    project = args.project or cfg.get("project")
    if not project:
        print("❌ Run: aegis init --project <id>", file=sys.stderr)
        sys.exit(1)
    data = _api("GET", f"/projects/{project}")
    print(f"📦 {data.get('name', data.get('id', '?'))}")
    print(f"   Repo: {data.get('repo_url', '?')}")
    summary = data.get("ticket_summary", {})
    if summary:
        parts = [f"{phase}: {count}" for phase, count in summary.items()]
        print(f"   Tickets: {', '.join(parts)}")
    dora = data.get("dora")
    if dora:
        print(f"   DORA: freq={dora.get('deployment_frequency', '?')}/day  "
              f"lead={dora.get('lead_time_ms', 0)//3600000}h  "
              f"fail={dora.get('change_failure_rate', 0):.0%}")


def cmd_create_ticket(args):
    """Create a new ticket."""
    cfg = _load_config()
    body = {
        "id": args.id,
        "title": args.title,
        "project_id": args.project or cfg.get("project", ""),
        "priority": args.priority or 3,
        "created_by": cfg.get("agent_id", "master"),
    }
    if args.checklist:
        body["checklist"] = args.checklist
    if args.description:
        body["description"] = args.description
    data = _api("POST", "/tickets", body)
    print(f"✅ Created {data.get('id', '?')}")
    print(f"   Phase: {data.get('phase', '?')}")
    print(f"   Project: {data.get('project_id', '(none)')}")


def cmd_logs(args):
    """View event log."""
    cfg = _load_config()
    params = []
    if args.ticket:
        params.append(f"ticket_id={args.ticket}")
    if args.limit:
        params.append(f"limit={args.limit}")
    qs = f"?{'&'.join(params)}" if params else ""
    data = _api("GET", f"/events{qs}")
    events = data.get("events", [])
    if not events:
        print("📭 No events")
        return
    for e in events[-20:]:
        ts = e.get("timestamp", "")
        # Convert ms to readable if numeric
        if isinstance(ts, (int, float)) and ts > 1e12:
            from datetime import datetime
            ts = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
        agent = e.get('agent_id', '') or ''
        tid = e.get('ticket_id', '') or ''
        action = e.get('event_type', e.get('action', '?'))
        old = e.get('old_value', '') or ''
        new = e.get('new_value', '') or ''
        detail = f" ({old}→{new})" if old and new else (f" → {new}" if new else "")
        agent_str = f" [{agent}]" if agent else ""
        print(f"  {ts}  {action:20s} {tid:15s}{agent_str}{detail}")


def cmd_roles(args):
    """List available roles."""
    data = _api("GET", "/roles")
    roles = data.get("roles", [])
    if not roles:
        print("📭 No roles defined")
        return
    print("📋 Available roles:\n")
    for r in roles:
        print(f"  🎭 {r.get('id', '?'):15s} {r.get('display_name', '')}")
        if r.get('description'):
            print(f"     {r['description'][:60]}")


def cmd_heartbeat(args):
    """Send heartbeat (keeps ticket lock alive)."""
    cfg = _load_config()
    agent_id = cfg.get("agent_id")
    if not agent_id:
        print("❌ Run: aegis init --agent-id <id>", file=sys.stderr)
        sys.exit(1)
    _api("POST", f"/agents/{agent_id}/heartbeat")
    print("💓 Heartbeat sent")


def cmd_canary(args):
    """Report canary metrics."""
    body = {
        "error_rate": args.error_rate or 0.0,
        "latency_p50_ms": args.latency_p50 or 0.0,
        "latency_p99_ms": args.latency_p99 or 0.0,
        "request_rate": args.request_rate or 0.0,
        "saturation": args.saturation or 0.0,
    }
    data = _api("POST", f"/tickets/{args.ticket_id}/canary/check", body)
    action = data.get("action", "?")
    icon = {"promote": "⬆️", "hold": "⏸️", "rollback": "🔴", "complete": "✅"}.get(action, "❓")
    print(f"{icon} Canary: {action}")
    if data.get("from") and data.get("to"):
        print(f"   {data['from']}% → {data['to']}%")


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="Aegis CLI — connect to the AI governance platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  aegis init --server http://aegis:9800 --project my-app --agent-id chris
  aegis register --id chris --provider gemini
  aegis tickets
  aegis claim PR-42
  aegis submit PR-42 --branch feature/fix
  aegis advance PR-42 --to code_review
  aegis deploy pre
  aegis project
        """)

    sub = parser.add_subparsers(dest="command")

    # init
    p = sub.add_parser("init", help="Configure Aegis CLI")
    p.add_argument("--server", help="Aegis server URL")
    p.add_argument("--project", help="Project ID")
    p.add_argument("--api-key", help="Project API key")
    p.add_argument("--agent-id", help="Your agent ID")

    # status
    sub.add_parser("status", help="Server health check")

    # register
    p = sub.add_parser("register", help="Register as an agent")
    p.add_argument("--id", help="Agent ID")
    p.add_argument("--provider", help="Provider (gemini/claude/gpt/human)")
    p.add_argument("--webhook", help="Webhook URL for notifications")

    # whoami
    sub.add_parser("whoami", help="Show current agent info")



    # tickets
    p = sub.add_parser("tickets", help="List tickets")
    p.add_argument("--project", help="Filter by project")
    p.add_argument("--phase", help="Filter by phase")

    # create
    p = sub.add_parser("create", help="Create a ticket")
    p.add_argument("id", help="Ticket ID")
    p.add_argument("title", help="Ticket title")
    p.add_argument("--project", help="Project ID")
    p.add_argument("--priority", type=int, help="Priority (1-5)")
    p.add_argument("--description", help="Description")
    p.add_argument("--checklist", nargs="+", help="Checklist items")

    # claim
    p = sub.add_parser("claim", help="Claim a ticket")
    p.add_argument("ticket_id", help="Ticket ID")

    # submit
    p = sub.add_parser("submit", help="Submit work for a ticket")
    p.add_argument("ticket_id", help="Ticket ID")
    p.add_argument("--branch", help="Git branch (for implementation)")
    p.add_argument("--commit", help="Commit SHA")
    p.add_argument("--verdict", help="Review verdict (pass/fail)")
    p.add_argument("--evidence-type", help="Evidence type")
    p.add_argument("--message", help="Evidence content / review message")

    # advance
    p = sub.add_parser("advance", help="Advance ticket phase (master)")
    p.add_argument("ticket_id", help="Ticket ID")
    p.add_argument("--to", required=True, help="Target phase")
    p.add_argument("--reason", help="Reason for advance")

    # reject
    p = sub.add_parser("reject", help="Reject ticket to rework")
    p.add_argument("ticket_id", help="Ticket ID")
    p.add_argument("--reason", required=True, help="Rejection reason")
    p.add_argument("--blockers", nargs="+", help="Blocker comments")

    # deploy
    p = sub.add_parser("deploy", help="Deploy to environment")
    p.add_argument("env", choices=["pre", "prod"], help="Target environment")

    # project
    p = sub.add_parser("project", help="Project dashboard")
    p.add_argument("--project", help="Project ID (defaults to configured)")

    # canary
    p = sub.add_parser("canary", help="Report canary metrics")
    p.add_argument("ticket_id", help="Ticket ID")
    p.add_argument("--error-rate", type=float, help="Error rate (0-1)")
    p.add_argument("--latency-p50", type=float, help="P50 latency ms")
    p.add_argument("--latency-p99", type=float, help="P99 latency ms")
    p.add_argument("--request-rate", type=float, help="Requests per second")
    p.add_argument("--saturation", type=float, help="Resource saturation (0-1)")

    # logs
    p = sub.add_parser("logs", help="View event log")
    p.add_argument("--ticket", help="Filter by ticket ID")
    p.add_argument("--limit", type=int, default=20, help="Max events")

    # roles
    sub.add_parser("roles", help="List available roles")

    # heartbeat
    sub.add_parser("heartbeat", help="Send heartbeat")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "init": cmd_init, "status": cmd_status, "register": cmd_register,
        "whoami": cmd_whoami,
        "tickets": cmd_tickets, "create": cmd_create_ticket, "claim": cmd_claim,
        "submit": cmd_submit, "advance": cmd_advance, "reject": cmd_reject,
        "deploy": cmd_deploy, "project": cmd_project, "canary": cmd_canary,
        "logs": cmd_logs, "roles": cmd_roles, "heartbeat": cmd_heartbeat,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()

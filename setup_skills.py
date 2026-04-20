#!/usr/bin/env python3
"""Aegis Skill Installer — install Aegis skills into any AI agent host.

Usage:
    python3 setup_skills.py                          # auto-detect hosts
    python3 setup_skills.py --host claude             # Claude Code only
    python3 setup_skills.py --host cursor             # Cursor only
    python3 setup_skills.py --host gemini             # Gemini / Antigravity
    python3 setup_skills.py --host all                # all detected hosts
    python3 setup_skills.py --project-local           # install into current project
    python3 setup_skills.py --server http://x:9800    # set server URL in skills
"""

import argparse
import os
import shutil
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"
SKILL_FILES = ["aegis-onboard.md", "aegis-worker.md", "aegis-coder.md", "aegis-reviewer.md", "aegis-master.md"]

# Where each AI agent host looks for skills
HOSTS = {
    "claude": Path.home() / ".claude" / "skills",
    "cursor": Path.home() / ".cursor" / "skills",
    "gemini": Path.home() / ".gemini" / "skills",
    "codex": Path.home() / ".codex" / "skills",
    "factory": Path.home() / ".factory" / "skills",
}


def detect_hosts() -> list[str]:
    """Detect which AI agent hosts are installed."""
    found = []
    for name, path in HOSTS.items():
        if path.parent.exists():
            found.append(name)
    return found or ["claude"]  # default to claude


def install_skills(host: str, server_url: str = "http://localhost:9800",
                   project_id: str = "", agent_id: str = ""):
    """Install Aegis skills into a host's skill directory."""
    target_dir = HOSTS.get(host)
    if not target_dir:
        print(f"❌ Unknown host: {host}")
        return False

    # Create aegis skill directory
    aegis_dir = target_dir / "aegis"
    aegis_dir.mkdir(parents=True, exist_ok=True)

    for skill_file in SKILL_FILES:
        src = SKILLS_DIR / skill_file
        if not src.exists():
            print(f"  ⚠️  {skill_file} not found, skipping")
            continue

        content = src.read_text()

        # Template substitution
        content = content.replace("{{AEGIS_SERVER}}", server_url)
        content = content.replace("{{AEGIS_HOST}}", server_url.split("://")[1].split(":")[0] if "://" in server_url else "localhost")
        content = content.replace("{{PROJECT_ID}}", project_id or "YOUR_PROJECT")
        content = content.replace("{{AGENT_ID}}", agent_id or "YOUR_AGENT_ID")
        content = content.replace("{{PROVIDER}}", _provider_for_host(host))
        content = content.replace("{{WEBHOOK_URL}}", "")

        # Write as SKILL.md (the format most agents expect)
        skill_name = skill_file.replace(".md", "")
        dest = aegis_dir / skill_file
        dest.write_text(content)
        print(f"  ✅ {skill_name} → {dest}")

    # Create index file
    index = aegis_dir / "README.md"
    index.write_text(f"""# Aegis Skills

Aegis governance platform skills. Server: {server_url}

| Skill | Command | Description |
|-------|---------|-------------|
| Onboard | `/aegis-onboard` | First-time setup |
| Coder | `/aegis-coder` | Claim tickets, implement, submit |
| Reviewer | `/aegis-reviewer` | Code review (anti-self-review) |
| Master | `/aegis-master` | Create tickets, advance, deploy |
| Worker | `/aegis-worker` | Combined coder + reviewer (中文) |

CLI: `aegis <command>` (or `python3 {SKILLS_DIR.parent / 'cli' / 'aegis.py'} <command>`)
""")

    return True


def install_project_local(server_url: str = "http://localhost:9800",
                          project_id: str = "", agent_id: str = ""):
    """Install skills into the current project's .aegis/ directory."""
    project_dir = Path.cwd()

    # Try common agent config locations
    for dirname in [".claude", ".cursor", ".agents"]:
        skill_dir = project_dir / dirname / "skills" / "aegis"
        skill_dir.mkdir(parents=True, exist_ok=True)

        for skill_file in SKILL_FILES:
            src = SKILLS_DIR / skill_file
            if not src.exists():
                continue
            content = src.read_text()
            content = content.replace("{{AEGIS_SERVER}}", server_url)
            content = content.replace("{{AEGIS_HOST}}", server_url.split("://")[1].split(":")[0] if "://" in server_url else "localhost")
            content = content.replace("{{PROJECT_ID}}", project_id or "YOUR_PROJECT")
            content = content.replace("{{AGENT_ID}}", agent_id or "YOUR_AGENT_ID")
            content = content.replace("{{PROVIDER}}", "unknown")
            content = content.replace("{{WEBHOOK_URL}}", "")
            dest = skill_dir / skill_file
            dest.write_text(content)

        print(f"  ✅ Installed to {skill_dir}")


def _provider_for_host(host: str) -> str:
    return {"claude": "claude", "cursor": "claude", "gemini": "gemini",
            "codex": "gpt", "factory": "unknown"}.get(host, "unknown")


def main():
    parser = argparse.ArgumentParser(description="Install Aegis skills into AI agent hosts")
    parser.add_argument("--host", help="Target host (claude/cursor/gemini/codex/all)")
    parser.add_argument("--server", default="http://localhost:9800", help="Aegis server URL")
    parser.add_argument("--project", default="", help="Project ID")
    parser.add_argument("--agent-id", default="", help="Agent ID")
    parser.add_argument("--project-local", action="store_true", help="Install into current project")
    args = parser.parse_args()

    print("🛡️  Aegis Skill Installer\n")

    if args.project_local:
        print("Installing project-local skills...")
        install_project_local(args.server, args.project, args.agent_id)
        return

    if args.host == "all":
        hosts = list(HOSTS.keys())
    elif args.host:
        hosts = [args.host]
    else:
        hosts = detect_hosts()
        print(f"Detected hosts: {', '.join(hosts)}\n")

    for host in hosts:
        print(f"📦 Installing to {host}...")
        install_skills(host, args.server, args.project, args.agent_id)
        print()

    print("Done! Skills available:")
    print("  /aegis-onboard   — First-time setup")
    print("  /aegis-coder     — Claim, implement, submit")
    print("  /aegis-reviewer  — Code review")
    print("  /aegis-master    — Ticket management, deploy")
    print()
    print("Quick start: open Claude Code and type /aegis-onboard")


if __name__ == "__main__":
    main()

# /aegis-onboard — First-Time Setup

> Run this skill once to connect any AI agent to the Aegis governance platform. After onboarding, use `/aegis-coder`, `/aegis-reviewer`, or `/aegis-master` based on your role.

## Step 1: Install the CLI

The Aegis CLI is a single Python file with zero dependencies (only stdlib). It works with Python 3.8+.

```bash
# Option A: Use directly from the repo
alias aegis="python3 /path/to/novaic-command-center/cli/aegis.py"

# Option B: Symlink for system-wide access
ln -sf /path/to/novaic-command-center/cli/aegis /usr/local/bin/aegis
```

Verify:
```bash
aegis --help
```

## Step 2: Configure

```bash
aegis init \
  --server http://{{AEGIS_HOST}}:9800 \
  --project {{PROJECT_ID}} \
  --agent-id {{YOUR_AGENT_ID}}
```

Choose your agent ID wisely:
- Human via Claude Code: `chris-claude`
- Automated Gemini: `gemini-worker-01`
- CI pipeline: `ci-bot`

## Step 3: Register

```bash
aegis register \
  --id {{YOUR_AGENT_ID}} \
  --provider {{PROVIDER}} \
  --webhook {{WEBHOOK_URL}}
```

Providers: `gemini`, `claude`, `gpt`, `human`, `unknown`

Webhook (optional): URL where Aegis sends notifications when:
- A ticket is assigned to you
- A review is needed
- A deployment happens
- A rollback triggers

## Step 4: Verify Everything

```bash
aegis status      # Server is reachable?
aegis whoami      # Agent registered?
aegis project     # Project accessible?
aegis tickets     # Can see tickets?
```

Expected output:
```
🟢 Aegis v1.0.0
   Projects: 3  Tickets: 12  Agents: 5

🤖 chris-claude (gemini)
   Status: idle
   Ready to claim tickets.
```

## Step 5: Start Working

Based on your role, load the appropriate skill:

| Role | Load this skill | What you do |
|------|-----------------|-------------|
| `coder` | `/aegis-coder` | Claim tickets, write code, submit for CI |
| `reviewer` | `/aegis-reviewer` | Review code, leave blockers, approve |
| `master` | `/aegis-master` | Create tickets, advance phases, deploy |

## Quick Reference Card

```
aegis status              → Server health
aegis whoami              → Your agent info
aegis tickets             → List available work
aegis claim <ID>          → Take a ticket
aegis submit <ID> --branch <B>  → Submit code
aegis submit <ID> --verdict pass  → Approve review
aegis advance <ID> --to <PHASE>  → Advance (master)
aegis reject <ID> --reason "..."  → Send back
aegis deploy pre|prod     → Deploy
aegis project             → Dashboard
aegis canary <ID>         → Report metrics
```

## Integrating with AI Agent Hosts

### Claude Code
Add to your project's `CLAUDE.md`:
```markdown
## Aegis
Use the Aegis CLI for all engineering governance.
Available skills: /aegis-onboard, /aegis-coder, /aegis-reviewer, /aegis-master
CLI location: novaic-command-center/cli/aegis.py
```

### Cursor
Add to `.cursor/rules`:
```
Use aegis CLI for ticket management and CI/CD.
Run: python3 novaic-command-center/cli/aegis.py <command>
```

### Gemini / Antigravity
Skills are auto-detected from `novaic-command-center/skills/`.

### Any Agent
Any tool that can run shell commands can use Aegis:
```bash
python3 /path/to/cli/aegis.py tickets
python3 /path/to/cli/aegis.py claim PR-42
```

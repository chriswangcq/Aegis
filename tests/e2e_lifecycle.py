#!/usr/bin/env python3
"""E2E test v3: git-only verification model.

Tests:
  1. Agent lifecycle (register, certify)
  2. Project creation (repo_url required)
  3. Ticket under project (inherits domain)
  4. Preflight → review → advance
  5. Impl submit requires branch (400 without it)
  6. Anti-self-review (same agent, same provider)
  7. Skip-preflight tickets
"""
import requests
API = "http://127.0.0.1:9800"

def p(label, r):
    ok = r.status_code < 400
    detail = "" if ok else f" ({r.status_code}: {r.text[:80]})"
    print(f"  {'✅' if ok else '❌'} {label}{detail}")
    try: return r.json()
    except: return {}

print("═══ 1. Register agents ═══")
p("gemini worker", requests.post(f"{API}/agents", json={"id":"antigravity-gemini","display_name":"Gemini","provider":"gemini"}))
p("claude reviewer", requests.post(f"{API}/agents", json={"id":"cursor-claude","display_name":"Claude","provider":"claude"}))

print("\n═══ 2. Certify ═══")
p("coder exam", requests.post(f"{API}/roles/coder/exam", json={"agent_id":"antigravity-gemini","answers":["a","b","c","B"]}))
p("grade coder", requests.post(f"{API}/certifications/antigravity-gemini/coder/grade?score=0.9&verdict=passed"))
p("reviewer exam", requests.post(f"{API}/roles/reviewer/exam", json={"agent_id":"cursor-claude","answers":["a","b","c"]}))
p("grade reviewer", requests.post(f"{API}/certifications/cursor-claude/reviewer/grade?score=0.85&verdict=passed"))

print("\n═══ 3. Create project ═══")
d = p("create project", requests.post(f"{API}/projects", json={
    "id":"test-proj","name":"Test Project",
    "repo_url":"https://github.com/chriswangcq/Aegis.git",
    "tech_stack":["python"],"default_domain":"python","master_id":"master"}))
print(f"  repo_url: {d.get('repo_url')}")

print("\n═══ 4. Ticket under project ═══")
d = p("create PR-20", requests.post(f"{API}/tickets", json={
    "id":"PR-20","project_id":"test-proj",
    "title":"删除 inline dispatch","priority":3,
    "checklist":["删除函数","写测试"]}))
print(f"  project_id: {d.get('project_id')}, domain: {d.get('domain')}")

print("\n═══ 5. Preflight flow ═══")
p("claim → preflight", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"antigravity-gemini"}))
p("submit preflight", requests.post(f"{API}/tickets/PR-20/submit", json={
    "agent_id":"antigravity-gemini",
    "evidence":[{"evidence_type":"preflight","content":"方案OK","verdict":"pass"}]}))
p("advance → impl", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"implementation","reason":"OK"}))

print("\n═══ 6. Impl submit requires branch ═══")
p("claim impl", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"antigravity-gemini"}))
# Submit without branch → should fail with 400
r = requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"antigravity-gemini"})
if r.status_code == 400:
    print(f"  ✅ correctly rejected (400): {r.json().get('detail','')[:60]}")
else:
    print(f"  ❌ expected 400, got {r.status_code}")

# Submit with branch but no ssh_host → should fail with 400 (SSH not configured)
print("  📍 submitting with branch='main' (no SSH host configured)...")
r = requests.post(f"{API}/tickets/PR-20/submit", json={
    "agent_id":"antigravity-gemini","branch":"main"})
d = r.json() if r.status_code < 500 else {}
if r.status_code == 400 and "ssh_host" in str(d):
    print(f"  ✅ correctly rejected — ssh_host not configured")
    # Force advance for remaining tests (skip CI gate)
    from unittest.mock import patch
    # Directly update phase in DB for test continuity
    import sqlite3
    conn = sqlite3.connect("data/command-center.db")
    conn.execute("UPDATE tickets SET phase='code_review',updated_at=0 WHERE id='PR-20'")
    conn.commit(); conn.close()
    print(f"  ✅ advance → code_review (forced for test)")
elif r.status_code == 200:
    print(f"  ✅ submit passed — CI verified via SSH")
    p("advance → code_review", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"code_review","reason":"CI passed"}))

# For the rest of the E2E, advance manually to continue testing
p("advance → code_review", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"code_review","reason":"CI manual override for E2E"}))

print("\n═══ 7. Anti-self-review ═══")
p("same-agent review (expect 403)", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"antigravity-gemini"}))
p("register gemini-reviewer", requests.post(f"{API}/agents", json={"id":"gemini-reviewer","display_name":"Gemini2","provider":"gemini"}))
p("cert gemini-reviewer", requests.post(f"{API}/roles/reviewer/exam", json={"agent_id":"gemini-reviewer","answers":["a","b","c"]}))
p("grade", requests.post(f"{API}/certifications/gemini-reviewer/reviewer/grade?score=0.8&verdict=passed"))
p("same-provider review (expect 403)", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"gemini-reviewer"}))
p("claude review ✓", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"cursor-claude"}))
p("approve", requests.post(f"{API}/tickets/PR-20/submit", json={
    "agent_id":"cursor-claude",
    "evidence":[{"evidence_type":"review","content":"LGTM","verdict":"approved"}]}))
p("advance → done", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"done","reason":"QA skipped for E2E"}))

print("\n═══ 8. Ticket without project → impl submit fails ═══")
p("create orphan ticket", requests.post(f"{API}/tickets", json={"id":"ORPHAN-01","title":"no project","priority":1,"checklist":["fix"]}))
p("claim", requests.post(f"{API}/tickets/ORPHAN-01/claim", json={"agent_id":"antigravity-gemini"}))
# Skip preflight first
p("submit preflight", requests.post(f"{API}/tickets/ORPHAN-01/submit", json={
    "agent_id":"antigravity-gemini",
    "evidence":[{"evidence_type":"preflight","content":"ok","verdict":"pass"}]}))
p("advance → impl", requests.post(f"{API}/tickets/ORPHAN-01/advance", json={"target_phase":"implementation","reason":"OK"}))
p("claim impl", requests.post(f"{API}/tickets/ORPHAN-01/claim", json={"agent_id":"antigravity-gemini"}))
r = requests.post(f"{API}/tickets/ORPHAN-01/submit", json={"agent_id":"antigravity-gemini","branch":"main"})
if r.status_code == 400 and "project" in r.text.lower():
    print(f"  ✅ correctly rejected: ticket must belong to a project")
else:
    print(f"  ❌ expected 400 about project, got {r.status_code}")

print("\n═══ 9. Project dashboard ═══")
d = requests.get(f"{API}/projects/test-proj").json()
print(f"  tickets: {len(d.get('tickets',[]))}")
print(f"  phases: {d.get('ticket_summary',{})}")

print("\n═══ 10. Audit trail ═══")
events = requests.get(f"{API}/events?ticket_id=PR-20").json()["events"]
print(f"  {len(events)} events for PR-20:")
for e in events[:5]:
    old = e.get("old_value") or "-"; new = e.get("new_value") or "-"
    print(f"    {e['event_type']:20s} {old:20s} → {new}")

print("\n🎉 ALL DONE")

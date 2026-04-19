#!/usr/bin/env python3
"""E2E test v2: simplified flow — no self_test, skip_preflight, anti-self-review."""
import requests
API = "http://127.0.0.1:9800"

def p(label, r):
    ok = r.status_code < 400
    detail = "" if ok else f" ({r.status_code}: {r.text[:80]})"
    print(f"  {'✅' if ok else '❌'} {label}{detail}")
    try: return r.json()
    except: return {}

print("═══ 1. Register agents (different providers!) ═══")
p("gemini worker", requests.post(f"{API}/agents", json={"id":"antigravity-gemini","display_name":"Gemini","provider":"gemini"}))
p("claude reviewer", requests.post(f"{API}/agents", json={"id":"cursor-claude","display_name":"Claude","provider":"claude"}))

print("\n═══ 2. Certify both ═══")
p("coder exam", requests.post(f"{API}/roles/coder/exam", json={"agent_id":"antigravity-gemini","answers":["a","b","c","B"]}))
p("grade coder", requests.post(f"{API}/certifications/antigravity-gemini/coder/grade?score=0.9&verdict=passed"))
p("reviewer exam", requests.post(f"{API}/roles/reviewer/exam", json={"agent_id":"cursor-claude","answers":["a","b","c"]}))
p("grade reviewer", requests.post(f"{API}/certifications/cursor-claude/reviewer/grade?score=0.85&verdict=passed"))

print("\n═══ 3. Normal ticket (with preflight) ═══")
p("create PR-20", requests.post(f"{API}/tickets", json={"id":"PR-20","title":"删除 inline dispatch","priority":3,"checklist":["删除函数","写测试"]}))
p("claim → preflight", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"antigravity-gemini"}))
p("submit preflight", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"antigravity-gemini","evidence":[{"evidence_type":"preflight","content":"方案OK","verdict":"pass"}]}))
p("advance → impl", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"implementation","reason":"OK"}))
p("claim impl", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"antigravity-gemini"}))
p("submit impl → code_review (直达!)", requests.post(f"{API}/tickets/PR-20/submit", json={
    "agent_id":"antigravity-gemini","evidence":[
        {"evidence_type":"stdout","content":"pytest: 10 passed","verdict":"pass"},
        {"evidence_type":"diff","content":"+45 -120","verdict":"pass"}
    ]}))
t = requests.get(f"{API}/tickets/PR-20").json()
print(f"  📍 Phase after impl submit: {t['phase']} (should be code_review)")

print("\n═══ 4. Anti-self-review ═══")
# Same agent tries to review → should fail
p("same-agent review (expect 403)", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"antigravity-gemini"}))
# Same provider (gemini) tries to review → need a gemini reviewer first
p("register gemini-reviewer", requests.post(f"{API}/agents", json={"id":"gemini-reviewer","display_name":"Gemini2","provider":"gemini"}))
p("cert gemini-reviewer", requests.post(f"{API}/roles/reviewer/exam", json={"agent_id":"gemini-reviewer","answers":["a","b","c"]}))
p("grade", requests.post(f"{API}/certifications/gemini-reviewer/reviewer/grade?score=0.8&verdict=passed"))
p("same-provider review (expect 403)", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"gemini-reviewer"}))
# Different provider (claude) reviews → should work
p("claude review ✓", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"cursor-claude"}))
p("approve", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"cursor-claude","evidence":[{"evidence_type":"review","content":"LGTM","verdict":"approved"}]}))
p("advance → done", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"done","reason":"QA skipped"}))

print("\n═══ 5. Skip-preflight ticket ═══")
d = p("create simple ticket", requests.post(f"{API}/tickets", json={"id":"FIX-01","title":"typo fix","priority":1,"skip_preflight":True,"checklist":["fix typo"]}))
print(f"  skip_preflight: {d.get('skip_preflight')}")
p("claim → implementation (skip preflight!)", requests.post(f"{API}/tickets/FIX-01/claim", json={"agent_id":"antigravity-gemini"}))
t = requests.get(f"{API}/tickets/FIX-01").json()
print(f"  📍 Phase: {t['phase']} (should be implementation, NOT preflight)")

print("\n═══ 6. Final audit ═══")
events = requests.get(f"{API}/events?ticket_id=PR-20").json()["events"]
print(f"  {len(events)} events for PR-20:")
for e in events:
    old = e.get("old_value") or "-"; new = e.get("new_value") or "-"
    print(f"    {e['event_type']:20s} {old:20s} → {new}")

print("\n🎉 ALL DONE")

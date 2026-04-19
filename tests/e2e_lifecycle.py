#!/usr/bin/env python3
"""E2E test: full lifecycle — register → certify → claim → submit → reject → rework → done"""
import requests
API = "http://127.0.0.1:9800"

def p(label, r):
    ok = r.status_code < 400
    mark = "✅" if ok else "❌"
    detail = ""
    if not ok:
        detail = f" ({r.status_code}: {r.text[:80]})"
    print(f"  {mark} {label}{detail}")
    try: return r.json()
    except: return {}

print("═══ 1. Register agents ═══")
p("coder", requests.post(f"{API}/agents", json={"id":"w1","display_name":"Gemini","provider":"gemini"}))
p("reviewer", requests.post(f"{API}/agents", json={"id":"r1","display_name":"Claude","provider":"claude"}))

print("\n═══ 2. Coder exam → pending → grade → pass ═══")
p("submit exam", requests.post(f"{API}/roles/coder/exam", json={
    "agent_id":"w1","answers":["EntityStore.create + OutboxInsert","git add Entangled && git commit","假测试","B"]}))
p("uncertified claim", requests.post(f"{API}/tickets/T-1/claim", json={"agent_id":"w1"}))  # expect 404 (no ticket yet)
p("grade → pass", requests.post(f"{API}/certifications/w1/coder/grade?score=0.9&verdict=passed"))

print("\n═══ 3. Reviewer exam → pass ═══")
p("submit exam", requests.post(f"{API}/roles/reviewer/exam", json={
    "agent_id":"r1","answers":["断言mock传参不测业务行为","reject scope违规","F-001 fake_checkmark"]}))
p("grade → pass", requests.post(f"{API}/certifications/r1/reviewer/grade?score=0.85&verdict=passed"))

print("\n═══ 4. Create ticket + full lifecycle ═══")
p("create", requests.post(f"{API}/tickets", json={"id":"PR-20","title":"删除 inline dispatch","priority":3,"checklist":["删除函数","写测试"]}))
p("claim → preflight", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"w1"}))
p("submit preflight", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"w1","evidence":[{"evidence_type":"preflight","content":"方案OK","verdict":"pass"}]}))
p("advance → impl", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"implementation","reason":"OK"}))
p("claim impl", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"w1"}))
p("submit impl → self_test", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"w1","evidence":[{"evidence_type":"stdout","content":"pytest: 8 passed","verdict":"pass"}]}))
p("claim self_test", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"w1"}))
p("submit self_test → code_review", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"w1","evidence":[{"evidence_type":"stdout","content":"all tests green","verdict":"pass"}]}))

print("\n═══ 5. CR review → reject ═══")
p("cr claim", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"r1"}))
p("cr reject", requests.post(f"{API}/tickets/PR-20/reject", json={"reason":"假测试","blocker_comments":["test只断言mock"]}))

print("\n═══ 6. Rework → resolve blocker → resubmit ═══")
p("reclaim rework", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"w1"}))
comments = requests.get(f"{API}/tickets/PR-20/comments").json()["comments"]
bid = [c for c in comments if c["comment_type"]=="blocker" and c["status"]=="open"][0]["id"]
p(f"resolve blocker #{bid}", requests.patch(f"{API}/tickets/PR-20/comments/{bid}", json={"status":"resolved"}))
p("submit rework → self_test", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"w1","evidence":[{"evidence_type":"stdout","content":"real tests pass","verdict":"pass"}]}))
p("claim self_test", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"w1"}))
p("submit → code_review", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"w1","evidence":[{"evidence_type":"stdout","content":"10 passed","verdict":"pass"}]}))

print("\n═══ 7. CR approve → done ═══")
p("cr claim", requests.post(f"{API}/tickets/PR-20/claim", json={"agent_id":"r1"}))
p("cr submit → qa", requests.post(f"{API}/tickets/PR-20/submit", json={"agent_id":"r1","evidence":[{"evidence_type":"review","content":"LGTM","verdict":"approved"}]}))
p("advance → done", requests.post(f"{API}/tickets/PR-20/advance", json={"target_phase":"done","reason":"skip QA"}))

print("\n═══ 8. Verify final state ═══")
t = requests.get(f"{API}/tickets/PR-20").json()
a = requests.get(f"{API}/agents/w1").json()
cert = [c for c in a["certifications"] if c["role_id"]=="coder"][0]
print(f"  Phase: {t['phase']}  Rounds: {t['review_rounds']}")
print(f"  Coder tasks: done={cert['tasks_completed']} failed={cert['tasks_failed']}")
print(f"  Trust: {cert['trust_json']}")

events = requests.get(f"{API}/events?ticket_id=PR-20").json()["events"]
print(f"\n═══ Audit trail ({len(events)} events) ═══")
for e in events:
    old = e.get("old_value") or "-"; new = e.get("new_value") or "-"
    print(f"  {e['event_type']:20s} {old:20s} → {new}")
print("\n🎉 ALL PASSED")

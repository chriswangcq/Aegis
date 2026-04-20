/* Aegis Dashboard — app.js */
const API = '';
let _cache = {};
let _apiKey = localStorage.getItem('aegis_api_key') || '';

function authHeaders() {
  const h = {'Content-Type': 'application/json'};
  if (_apiKey) h['Authorization'] = `Bearer ${_apiKey}`;
  return h;
}

async function api(path) {
  try {
    const r = await fetch(API + path, {headers: authHeaders()});
    if (r.status === 401) { showLogin('Session expired or invalid key'); return null; }
    if (!r.ok) throw new Error(r.statusText);
    return await r.json();
  } catch(e) { console.error(path, e); return null; }
}

function showLogin(msg) {
  document.querySelector('.app').style.display = 'none';
  let login = document.getElementById('login-screen');
  if (!login) {
    login = document.createElement('div');
    login.id = 'login-screen';
    login.innerHTML = `
      <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg-primary)">
        <div style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);padding:40px;width:400px;text-align:center">
          <div style="width:56px;height:56px;background:var(--gradient-blue);border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;color:white;margin:0 auto 16px;box-shadow:0 4px 16px rgba(59,130,246,0.3)">A</div>
          <h1 style="font-size:24px;font-weight:700;margin-bottom:4px">Aegis</h1>
          <p style="font-size:13px;color:var(--text-muted);margin-bottom:24px">Engineering Governance Platform</p>
          <div id="login-error" style="color:var(--accent-rose);font-size:13px;margin-bottom:12px;display:none"></div>
          <input id="login-key" type="password" placeholder="API Key" 
            style="width:100%;padding:10px 14px;background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-size:14px;margin-bottom:12px;outline:none"
            onkeydown="if(event.key==='Enter')doLogin()">
          <button onclick="doLogin()" class="btn btn-primary" style="width:100%;justify-content:center;padding:10px">Sign In</button>
          <p style="font-size:11px;color:var(--text-muted);margin-top:16px">Use your project API key or admin key</p>
        </div>
      </div>`;
    document.body.appendChild(login);
  }
  login.style.display = 'block';
  if (msg) { const e = document.getElementById('login-error'); e.textContent = msg; e.style.display = 'block'; }
  document.getElementById('login-key').focus();
}

async function doLogin() {
  const key = document.getElementById('login-key').value.trim();
  if (!key) return;
  try {
    const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({api_key: key})});
    if (!r.ok) { const d = await r.json(); document.getElementById('login-error').textContent = d.detail||'Invalid key'; document.getElementById('login-error').style.display='block'; return; }
    const data = await r.json();
    _apiKey = key;
    localStorage.setItem('aegis_api_key', key);
    document.getElementById('login-screen').style.display = 'none';
    document.querySelector('.app').style.display = 'flex';
    toast(`Logged in as ${data.role} (${data.project_id})`, 'success');
    loadOverview();
  } catch(e) { document.getElementById('login-error').textContent = 'Connection failed'; document.getElementById('login-error').style.display='block'; }
}

function toast(msg, type='info') {
  const c = document.getElementById('toast-container');
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

function fmtTime(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  return d.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}

function phaseBadge(phase) {
  const labels = {planning:'📋 Planning',ready:'🟢 Ready',preflight:'🔍 Preflight',
    preflight_review:'👀 PF Review',implementation:'🔨 Impl',rework:'🔄 Rework',
    code_review:'👀 Review',qa:'🧪 QA',monitoring:'📊 Monitor',done:'✅ Done'};
  return `<span class="phase-badge ${phase}">${labels[phase]||phase}</span>`;
}

function priorityDot(p) { return `<span class="priority-dot p${p}" title="P${p}"></span>`; }

// ── Navigation ──
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    item.classList.add('active');
    const sec = item.dataset.section;
    document.getElementById('section-' + sec).classList.add('active');
    const loaders = {overview:loadOverview, tickets:loadTickets, agents:loadAgents,
      projects:loadProjects, events:loadEvents, deploy:loadDeploy};
    if (loaders[sec]) loaders[sec]();
  });
});

function closeModal(e) {
  if (e && e.target.id !== 'modal-overlay') return;
  document.getElementById('modal-overlay').classList.remove('active');
}

function showModal(title, html) {
  document.getElementById('modal-body').innerHTML =
    `<div class="modal-title">${title}<button class="modal-close" onclick="closeModal({target:{id:'modal-overlay'}})">&times;</button></div>${html}`;
  document.getElementById('modal-overlay').classList.add('active');
}

// ── Overview ──
async function loadOverview() {
  const [status, tickets, dora, events] = await Promise.all([
    api('/status'), api('/tickets'), api('/metrics/dora'), api('/events?limit=10')
  ]);
  if (!status) { document.getElementById('server-dot').classList.add('offline');
    document.getElementById('server-status-text').textContent='Offline'; return; }
  document.getElementById('server-dot').classList.remove('offline');
  document.getElementById('server-status-text').textContent=`v${status.version}`;
  document.getElementById('nav-ticket-count').textContent = status.tickets;
  document.getElementById('nav-agent-count').textContent = status.agents;

  const tlist = tickets?.tickets || [];
  const phases = {};
  tlist.forEach(t => { phases[t.phase] = (phases[t.phase]||0) + 1; });

  document.getElementById('overview-metrics').innerHTML = [
    metricCard('Projects', status.projects, '', 'blue'),
    metricCard('Tickets', status.tickets, '', 'purple'),
    metricCard('Agents', status.agents, '', 'emerald'),
    metricCard('Active', tlist.filter(t=>t.assigned_to).length, 'in progress', 'amber')
  ].join('');

  if (dora) {
    document.getElementById('dora-metrics').innerHTML = [
      metricCard('Deploy Freq', dora.raw?.deployment_frequency?.toFixed(2)||'0', '/day', 'blue'),
      metricCard('Lead Time', dora.raw?.lead_time_ms ? (dora.raw.lead_time_ms/3600000).toFixed(1) : '0', 'hrs', 'emerald'),
      metricCard('Failure Rate', dora.raw?.change_failure_rate ? (dora.raw.change_failure_rate*100).toFixed(0) : '0', '%', 'rose'),
      metricCard('MTTR', dora.raw?.mttr_ms ? (dora.raw.mttr_ms/3600000).toFixed(1) : '0', 'hrs', 'amber')
    ].join('');
  }

  const phaseColors = {ready:'emerald',implementation:'blue',code_review:'purple',
    monitoring:'blue',done:'emerald',rework:'amber',planning:'rose'};
  document.getElementById('phase-distribution').innerHTML =
    Object.entries(phases).map(([p,c]) => metricCard(p, c, 'tickets', phaseColors[p]||'blue')).join('');

  renderTimeline('overview-timeline', events?.events || []);
}

function metricCard(label, value, unit, color) {
  return `<div class="metric-card ${color}"><div class="metric-label">${label}</div>
    <div class="metric-value">${value}<span class="metric-unit">${unit}</span></div></div>`;
}

// ── Tickets ──
async function loadTickets() {
  const pid = document.getElementById('ticket-project-filter').value;
  const qs = pid ? `?project_id=${pid}` : '';
  const data = await api('/tickets' + qs);
  const tlist = data?.tickets || [];
  const columns = ['ready','implementation','code_review','monitoring','rework','done'];
  const grouped = {};
  columns.forEach(c => grouped[c] = []);
  tlist.forEach(t => { if (grouped[t.phase]) grouped[t.phase].push(t); else {
    if (!grouped[t.phase]) grouped[t.phase] = []; grouped[t.phase].push(t); }});

  document.getElementById('ticket-kanban').innerHTML = columns.map(phase => {
    const items = grouped[phase] || [];
    return `<div class="kanban-column">
      <div class="kanban-header">${phaseBadge(phase)}<span class="kanban-count">${items.length}</span></div>
      <div class="kanban-body">${items.length ? items.map(t => ticketCard(t)).join('') :
        '<div class="empty-state" style="padding:30px"><p>Empty</p></div>'}</div></div>`;
  }).join('');

  // populate project filter
  const projects = await api('/projects');
  const sel = document.getElementById('ticket-project-filter');
  if (projects && sel.options.length <= 1) {
    (projects.projects||[]).forEach(p => {
      const o = document.createElement('option'); o.value=p.id; o.textContent=p.name||p.id; sel.appendChild(o);
    });
  }
}

function ticketCard(t) {
  return `<div class="ticket-card" onclick="showTicketDetail('${t.id}')">
    <div class="ticket-id">${t.id}</div>
    <div class="ticket-title">${(t.title||'').substring(0,50)}</div>
    <div class="ticket-meta">${priorityDot(t.priority||0)} P${t.priority||0}
      ${t.assigned_to ? `<span class="agent-badge">🤖 ${t.assigned_to}</span>` : ''}
      ${t.domain ? `<span style="color:var(--text-muted)">${t.domain}</span>` : ''}</div></div>`;
}

async function showTicketDetail(tid) {
  const t = await api(`/tickets/${tid}`);
  if (!t) return;
  const checklist = (t.checklist_json||[]).map(c =>
    `<div style="margin:4px 0;font-size:13px">${c.status==='done'?'✅':'⬜'} ${c.description}</div>`).join('');
  const evidence = (t.evidence||[]).map(e =>
    `<div style="margin:8px 0;padding:8px;background:var(--bg-glass);border-radius:6px;font-size:12px">
      <strong>${e.evidence_type}</strong> by ${e.agent_id} — ${e.verdict||'—'}
      <div style="color:var(--text-muted);margin-top:4px">${(e.content||'').substring(0,200)}</div></div>`).join('');
  const comments = (t.comments||[]).map(c =>
    `<div style="margin:8px 0;padding:8px;background:${c.comment_type==='blocker'?'rgba(244,63,94,0.05)':'var(--bg-glass)'};
      border-radius:6px;font-size:12px;border-left:3px solid ${c.comment_type==='blocker'?'var(--accent-rose)':'var(--border)'}">
      <strong>${c.author_id}</strong> <span style="color:var(--text-muted)">${c.comment_type}</span>
      ${c.status?`<span class="phase-badge ${c.status}" style="float:right">${c.status}</span>`:''}
      <div style="margin-top:4px">${c.content}</div></div>`).join('');

  showModal(t.id, `
    <div style="margin-bottom:12px">${phaseBadge(t.phase)} ${priorityDot(t.priority||0)} P${t.priority||0}
      ${t.assigned_to ? `<span class="agent-badge" style="margin-left:8px">🤖 ${t.assigned_to} (${t.assigned_role||''})</span>` : ''}
      ${t.risk_level!=='normal'?`<span style="color:var(--accent-rose);font-size:12px;margin-left:8px">⚠️ ${t.risk_level}</span>`:''}</div>
    <h3 style="font-size:16px;margin-bottom:8px">${t.title}</h3>
    <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px">${t.description||'No description'}</p>
    ${t.project_id?`<div style="font-size:12px;color:var(--text-muted);margin-bottom:12px">📦 ${t.project_id} ${t.domain?'· '+t.domain:''} · Round ${t.review_rounds||0}</div>`:''}
    ${checklist?`<h4 style="font-size:13px;color:var(--text-muted);margin:12px 0 8px">Checklist</h4>${checklist}`:''}
    ${t.open_blockers?`<div style="color:var(--accent-rose);font-size:13px;margin:12px 0">🚫 ${t.open_blockers} open blocker(s)</div>`:''}
    ${evidence?`<h4 style="font-size:13px;color:var(--text-muted);margin:16px 0 8px">Evidence</h4>${evidence}`:''}
    ${comments?`<h4 style="font-size:13px;color:var(--text-muted);margin:16px 0 8px">Comments</h4>${comments}`:''}
    <div style="font-size:11px;color:var(--text-muted);margin-top:16px">Created ${fmtTime(t.created_at)} · Updated ${fmtTime(t.updated_at)}</div>
  `);
}

function showCreateTicket() {
  showModal('Create Ticket', `
    <form onsubmit="createTicket(event)" style="display:flex;flex-direction:column;gap:12px">
      <input name="id" placeholder="Ticket ID (e.g. PR-42)" required class="btn" style="width:100%;text-align:left">
      <input name="title" placeholder="Title" required class="btn" style="width:100%;text-align:left">
      <textarea name="description" placeholder="Description" class="btn" style="width:100%;min-height:80px;text-align:left;resize:vertical"></textarea>
      <select name="project_id" class="btn" style="width:100%">
        <option value="">No project</option>
      </select>
      <div style="display:flex;gap:8px">
        <select name="priority" class="btn" style="flex:1"><option value="3">P3 Normal</option><option value="5">P5 Critical</option>
          <option value="4">P4 High</option><option value="2">P2 Low</option><option value="1">P1 Trivial</option></select>
        <select name="risk_level" class="btn" style="flex:1"><option value="normal">Normal</option><option value="high">High Risk</option></select>
      </div>
      <input name="checklist" placeholder="Checklist (comma-separated)" class="btn" style="width:100%;text-align:left">
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center">Create</button>
    </form>
    <script>
      api('/projects').then(d=>{const s=document.querySelector('[name=project_id]');
        (d?.projects||[]).forEach(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=p.name;s.appendChild(o)})});
    </script>
  `);
}

async function createTicket(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {id:f.get('id'),title:f.get('title'),description:f.get('description'),
    project_id:f.get('project_id'),priority:parseInt(f.get('priority')),
    risk_level:f.get('risk_level'),checklist:f.get('checklist')?f.get('checklist').split(',').map(s=>s.trim()).filter(Boolean):[]};
  const r = await fetch('/tickets', {method:'POST',headers:authHeaders(),body:JSON.stringify(body)});
  if (r.ok) { toast('Ticket created', 'success'); closeModal({target:{id:'modal-overlay'}}); loadTickets(); }
  else { const d = await r.json(); toast(d.detail||'Failed', 'error'); }
}

// ── Agents ──
async function loadAgents() {
  const data = await api('/agents');
  const agents = data?.agents || [];
  if (!agents.length) { document.getElementById('agent-grid').innerHTML =
    '<div class="empty-state"><div class="icon">🤖</div><p>No agents registered</p></div>'; return; }

  document.getElementById('agent-grid').innerHTML = agents.map(a => {
    const prov = a.provider || 'unknown';
    const initials = (a.display_name || a.id || '?').substring(0,2).toUpperCase();
    const certs = (a.certifications||[]).map(c =>
      `<span class="cert-badge ${c.status||'pending'}">${c.role_id} ${c.score?('('+c.score+')'):''}</span>`).join('');
    const trust = a.certifications?.length ? a.certifications.reduce((sum,c) => {
      const t = c.trust_json || {}; const vals = Object.values(t);
      return sum + (vals.length ? vals.reduce((a,b)=>a+b,0)/vals.length : 0.5);
    }, 0) / a.certifications.length : 0.5;
    const trustPct = Math.round(trust * 100);

    return `<div class="agent-card">
      <div class="agent-header">
        <div class="agent-avatar ${prov}">${initials}</div>
        <div><div class="agent-name">${a.display_name||a.id}</div><div class="agent-provider">${prov}</div></div>
        <span class="agent-status-badge ${a.status||'idle'}">${a.status||'idle'}</span>
      </div>
      ${a.current_ticket?`<div style="font-size:12px;color:var(--accent-amber);margin-bottom:8px">Working on: ${a.current_ticket} (${a.current_role||''})</div>`:''}
      <div style="font-size:12px;color:var(--text-muted)">Tasks: ${a.certifications?.reduce((s,c)=>s+(c.tasks_completed||0),0)||0} completed · ${a.certifications?.reduce((s,c)=>s+(c.tasks_failed||0),0)||0} failed</div>
      <div class="cert-list">${certs||'<span style="font-size:12px;color:var(--text-muted)">No certifications</span>'}</div>
      <div class="trust-bar"><div class="trust-label"><span>Trust Score</span><span>${trustPct}%</span></div>
        <div class="trust-track"><div class="trust-fill" style="width:${trustPct}%"></div></div></div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:8px">Last active: ${fmtTime(a.last_active_at)}</div>
    </div>`;
  }).join('');
}

// ── Projects ──
async function loadProjects() {
  const data = await api('/projects');
  const projects = data?.projects || [];
  if (!projects.length) { document.getElementById('projects-content').innerHTML =
    '<div class="empty-state"><div class="icon">📦</div><p>No projects</p></div>'; return; }

  let html = '';
  for (const p of projects) {
    const detail = await api(`/projects/${p.id}`);
    const envs = detail?.environments_json ? (typeof detail.environments_json === 'string' ? JSON.parse(detail.environments_json) : detail.environments_json) : {};
    const summary = detail?.ticket_summary || {};
    const dora = detail?.dora;

    html += `<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
        <div><h3 style="font-size:18px;font-weight:700">${detail?.name||p.id}</h3>
          <div style="font-size:12px;color:var(--text-muted);margin-top:2px">${p.id} · Master: ${detail?.master_id||'—'}</div></div>
        <a href="${detail?.repo_url||'#'}" target="_blank" class="btn" style="text-decoration:none">🔗 Repo</a>
      </div>
      <div class="metric-grid" style="margin-bottom:16px">
        ${Object.entries(summary).map(([phase,count]) => metricCard(phase, count, '', {ready:'emerald',implementation:'blue',code_review:'purple',done:'emerald',rework:'amber'}[phase]||'blue')).join('')}
      </div>
      ${dora ? `<div class="metric-grid">
        ${metricCard('Deploy Freq', dora.deployment_frequency?.toFixed(2)||'0', '/day', 'blue')}
        ${metricCard('Lead Time', dora.lead_time_ms?(dora.lead_time_ms/3600000).toFixed(1):'0', 'hrs', 'emerald')}
        ${metricCard('Failure Rate', dora.change_failure_rate?(dora.change_failure_rate*100).toFixed(0):'0', '%', 'rose')}
      </div>` : ''}
      <h4 style="font-size:13px;color:var(--text-muted);margin:16px 0 12px">Environments</h4>
      <div class="env-grid">
        ${['ci','pre','prod'].map(env => {
          const ec = envs[env] || {};
          const configured = !!ec.ssh_host;
          return `<div class="env-card">
            <div class="env-name"><span class="env-status-dot ${configured?'configured':'not-configured'}"></span>${env.toUpperCase()}</div>
            ${configured ? `
              <div class="env-detail"><span class="label">Host</span><span class="value">${ec.ssh_host}</span></div>
              <div class="env-detail"><span class="label">User</span><span class="value">${ec.ssh_user||'root'}:${ec.ssh_port||22}</span></div>
              ${ec.test_command?`<div class="env-detail"><span class="label">Test</span><span class="value">${ec.test_command}</span></div>`:''}
              ${ec.deploy_command?`<div class="env-detail"><span class="label">Deploy</span><span class="value">${ec.deploy_command.substring(0,40)}</span></div>`:''}
              ${ec.health_check_url?`<div class="env-detail"><span class="label">Health</span><span class="value">${ec.health_check_url}</span></div>`:''}
            ` : '<div style="font-size:12px;color:var(--text-muted);padding:8px 0">Not configured</div>'}
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }
  document.getElementById('projects-content').innerHTML = html;
}

// ── Events ──
async function loadEvents() {
  const filter = document.getElementById('event-filter-ticket')?.value || '';
  const qs = filter ? `?ticket_id=${filter}&limit=50` : '?limit=50';
  const data = await api('/events' + qs);
  renderTimeline('event-timeline', data?.events || []);
}

function renderTimeline(containerId, events) {
  const el = document.getElementById(containerId);
  if (!events.length) { el.innerHTML = '<div class="empty-state"><p>No events</p></div>'; return; }

  const dotClass = (type) => {
    if (type?.includes('claim')) return 'claimed';
    if (type?.includes('submit')) return 'submitted';
    if (type?.includes('advance') || type?.includes('promote')) return 'advanced';
    if (type?.includes('reject') || type?.includes('rollback')) return 'rejected';
    if (type?.includes('deploy')) return 'deployed';
    return 'default';
  };

  el.innerHTML = events.map(e => {
    const detail = [e.old_value, e.new_value].filter(Boolean).join(' → ');
    return `<div class="event-item">
      <div class="event-dot ${dotClass(e.event_type)}"></div>
      <div class="event-header">
        <span class="event-type">${e.event_type||'—'}</span>
        ${e.ticket_id?`<span class="event-ticket">${e.ticket_id}</span>`:''}
        ${e.agent_id?`<span class="event-agent">[${e.agent_id}]</span>`:''}
        <span class="event-time">${fmtTime(e.timestamp)}</span>
      </div>
      ${detail?`<div class="event-detail">${detail}</div>`:''}
    </div>`;
  }).join('');
}

// ── Deploy ──
async function loadDeploy() {
  const data = await api('/projects');
  const projects = data?.projects || [];
  if (!projects.length) { document.getElementById('deploy-content').innerHTML =
    '<div class="empty-state"><div class="icon">🚀</div><p>No projects</p></div>'; return; }

  let html = '';
  for (const p of projects) {
    const detail = await api(`/projects/${p.id}`);
    const envs = detail?.environments_json ? (typeof detail.environments_json === 'string' ? JSON.parse(detail.environments_json) : detail.environments_json) : {};
    html += `<div style="margin-bottom:24px">
      <h3 style="font-size:16px;font-weight:600;margin-bottom:12px">📦 ${detail?.name||p.id}</h3>
      <div class="env-grid">
        ${['pre','prod'].map(env => {
          const ec = envs[env] || {};
          const configured = !!ec.ssh_host && !!ec.deploy_command;
          return `<div class="env-card">
            <div class="env-name"><span class="env-status-dot ${configured?'configured':'not-configured'}"></span>${env.toUpperCase()}</div>
            ${configured ? `
              <div class="env-detail"><span class="label">Host</span><span class="value">${ec.ssh_host}</span></div>
              <button class="btn btn-primary" style="width:100%;justify-content:center;margin-top:12px"
                onclick="deployTo('${p.id}','${env}')">🚀 Deploy to ${env.toUpperCase()}</button>
            ` : '<div style="font-size:12px;color:var(--text-muted)">Not configured</div>'}
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }
  document.getElementById('deploy-content').innerHTML = html;
}

async function deployTo(pid, env) {
  if (!confirm(`Deploy ${pid} to ${env.toUpperCase()}?`)) return;
  toast(`Deploying to ${env}...`, 'info');
  const r = await fetch(`/projects/${pid}/deploy/${env}`, {method:'POST',headers:authHeaders()});
  const d = await r.json();
  if (r.ok) toast(`Deployed to ${env}: ${d.status}`, d.status==='ok'?'success':'error');
  else toast(d.detail||'Deploy failed', 'error');
}

// ── Refresh ──
async function refreshAll() {
  const active = document.querySelector('.nav-item.active')?.dataset.section || 'overview';
  const loaders = {overview:loadOverview, tickets:loadTickets, agents:loadAgents,
    projects:loadProjects, events:loadEvents, deploy:loadDeploy};
  if (loaders[active]) await loaders[active]();
  toast('Refreshed', 'success');
}

// Auto-refresh every 30s
setInterval(() => {
  const active = document.querySelector('.nav-item.active')?.dataset.section;
  if (active === 'overview') loadOverview();
}, 30000);

// ── Init ──
loadOverview();

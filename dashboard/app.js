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
          <div id="login-form">
            <input id="login-user" type="text" placeholder="用户名" autocomplete="username"
              style="width:100%;padding:10px 14px;background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-size:14px;margin-bottom:8px;outline:none">
            <input id="login-pass" type="password" placeholder="密码" autocomplete="current-password"
              style="width:100%;padding:10px 14px;background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-size:14px;margin-bottom:8px;outline:none"
              onkeydown="if(event.key==='Enter')doLogin()">
            <div id="register-fields" style="display:none">
              <input id="reg-name" type="text" placeholder="显示名称（选填）"
                style="width:100%;padding:10px 14px;background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-size:14px;margin-bottom:8px;outline:none">
              <input id="reg-email" type="email" placeholder="邮箱（选填）"
                style="width:100%;padding:10px 14px;background:var(--bg-glass);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-size:14px;margin-bottom:8px;outline:none">
            </div>
            <button id="login-btn" onclick="doLogin()" class="btn btn-primary" style="width:100%;justify-content:center;padding:10px">登 录</button>
            <p style="font-size:12px;color:var(--text-muted);margin-top:12px;cursor:pointer" onclick="toggleRegister()">
              <span id="toggle-text">没有账号？点击注册</span>
            </p>
          </div>
        </div>
      </div>`;
    document.body.appendChild(login);
  }
  login.style.display = 'block';
  if (msg) { const e = document.getElementById('login-error'); e.textContent = msg; e.style.display = 'block'; }
  document.getElementById('login-user').focus();
}

let _isRegister = false;
function toggleRegister() {
  _isRegister = !_isRegister;
  document.getElementById('register-fields').style.display = _isRegister ? 'block' : 'none';
  document.getElementById('login-btn').textContent = _isRegister ? '注 册' : '登 录';
  document.getElementById('toggle-text').textContent = _isRegister ? '已有账号？返回登录' : '没有账号？点击注册';
  document.getElementById('login-error').style.display = 'none';
}

async function doLogin() {
  const userId = document.getElementById('login-user').value.trim();
  const password = document.getElementById('login-pass').value;
  if (!userId || !password) { document.getElementById('login-error').textContent='请输入用户名和密码'; document.getElementById('login-error').style.display='block'; return; }

  const errEl = document.getElementById('login-error');
  try {
    if (_isRegister) {
      const r = await fetch('/api/register', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({user_id: userId, password, display_name: document.getElementById('reg-name').value, email: document.getElementById('reg-email').value})});
      const d = await r.json();
      if (!r.ok) { errEl.textContent = d.detail||'注册失败'; errEl.style.display='block'; return; }
      toast('注册成功！正在登录...', 'success');
      _isRegister = false;
    }
    // Login
    const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({user_id: userId, password})});
    const data = await r.json();
    if (!r.ok) { errEl.textContent = data.detail||'登录失败'; errEl.style.display='block'; return; }
    _apiKey = data.api_key;
    localStorage.setItem('aegis_api_key', data.api_key);
    localStorage.setItem('aegis_user', JSON.stringify({id: data.user_id, name: data.display_name||data.user_id, role: data.role}));
    document.getElementById('login-screen').style.display = 'none';
    document.querySelector('.app').style.display = 'flex';
    toast(`欢迎, ${data.display_name||data.user_id}`, 'success');
    loadOverview();
  } catch(e) { errEl.textContent = '连接失败'; errEl.style.display='block'; }
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
      projects:loadProjects, events:loadEvents, deploy:loadDeploy,
      team:loadTeam, notifications:loadNotifications};
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

    return `<div class="agent-card">
      <div class="agent-header">
        <div class="agent-avatar ${prov}">${initials}</div>
        <div><div class="agent-name">${a.display_name||a.id}</div><div class="agent-provider">${prov}</div></div>
        <span class="agent-status-badge ${a.status||'idle'}">${a.status||'idle'}</span>
      </div>
      ${a.current_ticket?`<div style="font-size:12px;color:var(--accent-amber);margin-bottom:8px">Working on: ${a.current_ticket} (${a.current_role||''})</div>`:''}
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

// ── Team ──
async function loadTeam() {
  const projects = await api('/projects');
  const plist = projects?.projects || [];
  if (!plist.length) { document.getElementById('team-content').innerHTML =
    '<div class="empty-state"><div class="icon">👥</div><p>No projects</p></div>'; return; }

  let html = '';
  for (const p of plist) {
    const [membersData, requestsData] = await Promise.all([
      api(`/api/projects/${p.id}/members`),
      api(`/api/projects/${p.id}/join-requests?status=pending`)
    ]);
    const members = membersData?.members || [];
    const requests = requestsData?.requests || [];

    html += `<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
        <h3 style="font-size:18px;font-weight:700">${p.name||p.id}</h3>
        <button class="btn btn-primary" onclick="showInviteModal('${p.id}')">+ 邀请成员</button>
      </div>
      <div style="display:grid;gap:8px;margin-bottom:16px">
        ${members.map(m => `<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;background:var(--bg-glass);border-radius:var(--radius-sm)">
          <div style="width:32px;height:32px;border-radius:50%;background:${m.role==='owner'?'var(--gradient-blue)':'var(--gradient-emerald)'};display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;color:white">${(m.display_name||m.user_id||'?').substring(0,1).toUpperCase()}</div>
          <div style="flex:1"><div style="font-size:14px;font-weight:500">${m.display_name||m.user_id}</div>
            <div style="font-size:11px;color:var(--text-muted)">${m.email||''}</div></div>
          <span class="phase-badge ${m.role==='owner'?'done':'ready'}">${m.role}</span>
        </div>`).join('')}
      </div>
      ${requests.length ? `<h4 style="font-size:14px;color:var(--accent-amber);margin-bottom:8px">📋 待审核 (${requests.length})</h4>
        ${requests.map(r => `<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;background:rgba(245,158,11,0.05);border:1px solid rgba(245,158,11,0.2);border-radius:var(--radius-sm);margin-bottom:8px">
          <div style="flex:1"><strong>${r.display_name||r.user_id}</strong> 申请以 ${r.role} 加入
            ${r.message?`<div style="font-size:12px;color:var(--text-muted);margin-top:4px">留言: ${r.message}</div>`:''}</div>
          <button class="btn btn-primary" style="padding:4px 12px;font-size:12px" onclick="reviewJoin(${r.id},'approved')">✓ 同意</button>
          <button class="btn" style="padding:4px 12px;font-size:12px;color:var(--accent-rose)" onclick="reviewJoin(${r.id},'rejected')">✗ 拒绝</button>
        </div>`).join('')}` : ''}
    </div>`;
  }
  document.getElementById('team-content').innerHTML = html;
}

function showInviteModal(pid) {
  showModal('邀请成员', `
    <form onsubmit="doInvite(event,'${pid}')" style="display:flex;flex-direction:column;gap:12px">
      <input name="user_id" placeholder="用户名" required class="btn" style="width:100%;text-align:left">
      <select name="role" class="btn" style="width:100%">
        <option value="member">Member</option>
        <option value="viewer">Viewer</option>
      </select>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center">邀请</button>
    </form>
  `);
}

async function doInvite(e, pid) {
  e.preventDefault();
  const f = new FormData(e.target);
  const r = await fetch(`/api/projects/${pid}/invite`, {method:'POST', headers:authHeaders(),
    body: JSON.stringify({user_id: f.get('user_id'), role: f.get('role')})});
  const d = await r.json();
  if (r.ok) { toast(d.message||'已邀请', 'success'); closeModal({target:{id:'modal-overlay'}}); loadTeam(); }
  else toast(d.detail||'邀请失败', 'error');
}

async function reviewJoin(reqId, action) {
  const note = action === 'rejected' ? prompt('拒绝理由（可选）:') || '' : '';
  const r = await fetch(`/api/join-requests/${reqId}/review`, {method:'POST', headers:authHeaders(),
    body: JSON.stringify({action, note})});
  const d = await r.json();
  if (r.ok) { toast(d.message||'已处理', 'success'); loadTeam(); }
  else toast(d.detail||'操作失败', 'error');
}

// ── Notifications ──
async function loadNotifications() {
  const data = await api('/api/notifications');
  if (!data) return;
  const notes = data.notifications || [];
  updateNotifBadge(data.unread_count);

  if (!notes.length) { document.getElementById('notif-list').innerHTML =
    '<div class="empty-state"><p>没有通知</p></div>'; return; }

  document.getElementById('notif-list').innerHTML = notes.map(n => {
    const isUnread = !n.is_read;
    return `<div class="event-item" style="${isUnread?'border-left:3px solid var(--accent-blue);padding-left:12px':''}">
      <div class="event-dot ${isUnread?'advanced':'default'}"></div>
      <div class="event-header">
        <span class="event-type" style="font-size:14px">${n.title}</span>
        <span class="event-time">${fmtTime(n.created_at)}</span>
      </div>
      <div class="event-detail">${n.body}</div>
      ${isUnread?`<button class="btn" style="padding:2px 8px;font-size:11px;margin-top:4px" onclick="markRead(${n.id})">标为已读</button>`:''}
    </div>`;
  }).join('');
}

async function markRead(nid) {
  await fetch(`/api/notifications/${nid}/read`, {method:'POST', headers:authHeaders()});
  loadNotifications();
}

async function markAllRead() {
  await fetch('/api/notifications/read-all', {method:'POST', headers:authHeaders()});
  toast('全部标为已读', 'success');
  loadNotifications();
}

function updateNotifBadge(count) {
  const badge = document.getElementById('nav-notif-count');
  if (count > 0) { badge.textContent = count; badge.style.display = 'inline-flex'; }
  else badge.style.display = 'none';
}

// ── Refresh ──
async function refreshAll() {
  const active = document.querySelector('.nav-item.active')?.dataset.section || 'overview';
  const loaders = {overview:loadOverview, tickets:loadTickets, agents:loadAgents,
    projects:loadProjects, events:loadEvents, deploy:loadDeploy,
    team:loadTeam, notifications:loadNotifications};
  if (loaders[active]) await loaders[active]();
  toast('Refreshed', 'success');
}

// Poll notification badge every 30s
setInterval(async () => {
  if (!_apiKey) return;
  const active = document.querySelector('.nav-item.active')?.dataset.section;
  if (active === 'overview') loadOverview();
  // Always poll notifications badge
  const data = await api('/api/notifications?unread_only=true');
  if (data) updateNotifBadge(data.unread_count);
}, 30000);

// ── Init ──
loadOverview();
// Initial notification check
setTimeout(async () => {
  if (!_apiKey) return;
  const data = await api('/api/notifications?unread_only=true');
  if (data) updateNotifBadge(data.unread_count);
}, 2000);

import { api, showToast, esc, initTheme, toggleTheme } from './common.js';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const fmt = (n) => Number(n || 0).toLocaleString();

initTheme();
$('#themeToggle').addEventListener('click', () => {
  toggleTheme();
  // Chart.js 颜色在创建时固化，主题切换后需重建图表以更换刻度/网格配色
  if (hourlyChart || userChart) loadStats();
});

// ---- 侧边栏：移动端抽屉 + 桌面折叠记忆 ----
// 折叠状态挂在 <html> 上（head 内联脚本已做防闪烁预置）
const rootEl = document.documentElement;

function openSidebar() {
  rootEl.classList.add('sidebar-open');
  $('#sidebarToggle').setAttribute('aria-expanded', 'true');
  $('#sidebarScrim').hidden = false;
}

function closeSidebar() {
  rootEl.classList.remove('sidebar-open');
  $('#sidebarToggle').setAttribute('aria-expanded', 'false');
  $('#sidebarScrim').hidden = true;
}

$('#sidebarToggle').addEventListener('click', () => {
  rootEl.classList.contains('sidebar-open') ? closeSidebar() : openSidebar();
});
$('#sidebarScrim').addEventListener('click', closeSidebar);

const SIDEBAR_KEY = 'nekoseek-sidebar';
$('#sidebarCollapse').setAttribute('aria-pressed', String(rootEl.classList.contains('sidebar-collapsed')));
$('#sidebarCollapse').addEventListener('click', () => {
  const collapsed = rootEl.classList.toggle('sidebar-collapsed');
  $('#sidebarCollapse').setAttribute('aria-pressed', String(collapsed));
  localStorage.setItem(SIDEBAR_KEY, collapsed ? 'collapsed' : 'expanded');
});

// ---- Tab 切换 ----
$$('#adminTabs .nav-link').forEach((btn) => {
  btn.addEventListener('click', () => {
    $$('#adminTabs .nav-link').forEach((b) => b.classList.remove('active'));
    $$('section.page').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    $(`#page-${btn.dataset.page}`).classList.add('active');
    // 移动端选择页面后自动收起抽屉
    if (window.innerWidth < 992) closeSidebar();
    switch (btn.dataset.page) {
      case 'overview': loadOverview(); break;
      case 'users': loadUsers(); break;
      case 'groups': loadGroups(); break;
      case 'invites': loadInvites(); break;
      case 'quota': loadQuota(); break;
      case 'stats': loadStats(); break;
      case 'logs': loadLogs(); break;
    }
  });
});

// ---- 概览 ----
async function loadOverview() {
  const chk = await api('/api/v1/auth/check');
  if (chk.data && chk.data.username) {
    $('#currentUser').textContent = `当前用户：${esc(chk.data.username)}`;
  }

  const [users, dsh, balance, statsRes, dsKey] = await Promise.all([
    api('/api/v1/admin/users'),
    api('/api/v1/admin/dsh/status'),
    api('/api/v1/admin/deepseek/balance'),
    api('/api/v1/admin/stats/overview'),
    api('/api/v1/admin/deepseek/apikey'),
  ]);

  const list = users.data || [];
  const stats = $('#overviewStats');

  const today = statsRes.data?.today || {};
  const allTime = statsRes.data?.all_time || {};
  const sinceHtml = allTime.since
    ? `自 ${new Date(allTime.since * 1000).toLocaleDateString()}`
    : '暂无记录';

  let balanceHtml = '<p class="card-text fs-3 fw-semibold mb-0 text-body-secondary">查询失败</p>';
  if (balance.data && balance.data.ok && (balance.data.balances || []).length) {
    const items = balance.data.balances.map((b) =>
      `<p class="card-text mb-0"><span class="fs-3 fw-semibold">${esc(b.total)}</span> <span class="text-body-secondary small">${esc(b.currency)}</span></p>`
    ).join('');
    balanceHtml = items + (balance.data.is_available === false
      ? '<p class="card-text text-danger small mb-0">账户不可用</p>' : '');
  } else if (balance.data && balance.data.error) {
    balanceHtml = `<p class="card-text fs-6 mb-0 text-body-secondary">${esc(balance.data.error)}</p>`;
  }

  stats.innerHTML = `
    <div class="col">
      <div class="card h-100">
        <div class="card-body">
          <h6 class="card-subtitle mb-2 text-body-secondary">用户数</h6>
          <p class="card-text fs-3 fw-semibold mb-0">${list.length}</p>
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card h-100">
        <div class="card-body">
          <h6 class="card-subtitle mb-2 text-body-secondary">管理员数</h6>
          <p class="card-text fs-3 fw-semibold mb-0">${list.filter((u) => u.is_admin).length}</p>
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card h-100">
        <div class="card-body">
          <h6 class="card-subtitle mb-2 text-body-secondary">DSH 状态</h6>
          <p class="card-text fs-3 fw-semibold mb-0">${dsh.data?.running ? '运行中' : '已停止'}</p>
          <p class="card-text text-body-secondary small mb-0">${dsh.data?.pid ? `PID ${dsh.data.pid}` : ''}</p>
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card h-100">
        <div class="card-body">
          <h6 class="card-subtitle mb-2 text-body-secondary">DeepSeek 余额</h6>
          ${balanceHtml}
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card h-100">
        <div class="card-body">
          <h6 class="card-subtitle mb-2 text-body-secondary">今日用量</h6>
          <p class="card-text fs-3 fw-semibold mb-0">${fmt(today.total_tokens || 0)}</p>
          <p class="card-text text-body-secondary small mb-0">输入 ${fmt(today.input_tokens || 0)} / 输出 ${fmt(today.output_tokens || 0)} · 活跃 ${today.active_users || 0} 人</p>
        </div>
      </div>
    </div>
    <div class="col">
      <div class="card h-100">
        <div class="card-body">
          <h6 class="card-subtitle mb-2 text-body-secondary">累计用量</h6>
          <p class="card-text fs-3 fw-semibold mb-0">${fmt(allTime.total_tokens || 0)}</p>
          <p class="card-text text-body-secondary small mb-0">输入 ${fmt(allTime.input_tokens || 0)} / 输出 ${fmt(allTime.output_tokens || 0)} · ${sinceHtml}</p>
        </div>
      </div>
    </div>
  `;

  const statusEl = $('#dshStatus');
  if (dsh.data?.running) {
    statusEl.className = 'badge text-bg-success';
    statusEl.textContent = `运行中 (PID ${dsh.data.pid})`;
  } else {
    statusEl.className = 'badge text-bg-secondary';
    statusEl.textContent = '已停止';
  }

  const keyEl = $('#dsKeyCurrent');
  if (dsKey.data && dsKey.data.configured) {
    keyEl.textContent = dsKey.data.masked;
  } else {
    keyEl.textContent = '未配置';
  }
}

$('#dsKeySave').addEventListener('click', async () => {
  const input = $('#dsKeyInput');
  const key = input.value.trim();
  if (!key) {
    showMsg($('#dsKeyMsg'), { code: 0, msg: '请输入 API Key' });
    return;
  }
  const btn = $('#dsKeySave');
  btn.disabled = true;
  const r = await api('/api/v1/admin/deepseek/apikey', {
    method: 'PUT',
    body: JSON.stringify({ api_key: key }),
  });
  btn.disabled = false;
  showMsg($('#dsKeyMsg'), r);
  if (r.code === 1) {
    input.value = '';
    loadOverview();
  }
});

$('#dsKeyToggle').addEventListener('click', () => {
  const input = $('#dsKeyInput');
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  $('#dsKeyToggle').textContent = show ? '隐藏' : '显示';
});

// DSH 启停：点击即给反馈（禁用按钮 + 状态徽标转「操作中…」），完成后刷新概览。
async function dshAction(btn, action, busyText) {
  const statusEl = $('#dshStatus');
  btn.disabled = true;
  statusEl.className = 'badge text-bg-warning';
  statusEl.textContent = busyText;
  try {
    const r = await api(`/api/v1/admin/dsh/${action}`, { method: 'POST' });
    showMsg('dshMsg', r);
  } finally {
    btn.disabled = false;
    // 无论成败都重取真实状态，避免与后端不一致
    await loadOverview();
  }
}

$('#dshStart').addEventListener('click', (e) => dshAction(e.currentTarget, 'start', '启动中…'));
$('#dshStop').addEventListener('click', (e) => dshAction(e.currentTarget, 'stop', '停止中…'));

// ---- 用户 ----
let allUsers = [];
async function loadUsers() {
  const [users, groups] = await Promise.all([
    api('/api/v1/admin/users'),
    api('/api/v1/admin/groups'),
  ]);
  allUsers = users.data || [];
  const gmap = Object.fromEntries((groups.data || []).map((g) => [g.id, g]));

  const tbody = $('#usersTable tbody');
  tbody.innerHTML = allUsers.map((u) => {
    const g = gmap[u.group_id];
    return `
      <tr data-user-id="${u.id}">
        <td>${u.id}</td>
        <td>${esc(u.username)}</td>
        <td><span class="badge ${g?.is_admin ? 'text-bg-primary' : 'text-bg-secondary'}">${esc(g?.name || u.group_id)}</span></td>
        <td><span class="badge ${u.status ? 'text-bg-success' : 'text-bg-danger'}">${u.status ? '启用' : '停用'}</span></td>
        <td>${u.quota_override ?? '继承组'}</td>
        <td class="table-actions">
          <button type="button" class="btn btn-sm btn-outline-primary me-1" data-action="edit-user">编辑</button>
          <button type="button" class="btn btn-sm btn-outline-danger" data-action="delete-user">删除</button>
        </td>
      </tr>`;
  }).join('');

  const sel = $('#editUserSelect');
  const cur = sel.value;
  sel.innerHTML = allUsers.map((u) => `<option value="${u.id}">${esc(u.username)} (#${u.id})</option>`).join('');
  if (cur) sel.value = cur;

  const gsel = $('#editGroup');
  gsel.innerHTML = (groups.data || []).map((g) => `<option value="${g.id}">${esc(g.name)}${g.is_admin ? ' (admin)' : ''}</option>`).join('');
}

function selectUser(id) {
  $('#editUserSelect').value = id;
  const u = allUsers.find((x) => x.id === id);
  if (!u) return;
  $('#editGroup').value = u.group_id;
  $('#editQuota').value = u.quota_override ?? '';
  $('#editStatus').value = u.status;
  $('#editPassword').value = '';
}

// 切换目标用户时同步表单为该用户当前值
$('#editUserSelect').addEventListener('change', () => {
  selectUser(parseInt($('#editUserSelect').value));
});

async function deleteUser(id) {
  if (!confirm(`确认删除用户 #${id}？`)) return;
  const r = await api(`/api/v1/admin/users/${id}`, { method: 'DELETE' });
  showMsg('userMsg', r);
  loadUsers();
}

$('#saveUser').addEventListener('click', async () => {
  const id = parseInt($('#editUserSelect').value);
  const body = {
    group_id: parseInt($('#editGroup').value),
    status: parseInt($('#editStatus').value),
  };
  const q = $('#editQuota').value;
  // 留空 = 继承组：显式发 null 让后端清除覆写；fillna 除外
  body.quota_override = q === '' ? null : parseInt(q);
  const pw = $('#editPassword').value;
  if (pw !== '') body.password = pw;
  const r = await api(`/api/v1/admin/users/${id}`, { method: 'PUT', body: JSON.stringify(body) });
  showMsg('userMsg', r);
  loadUsers();
});

// ---- 权限组 ----
let allGroups = [];

async function loadGroups() {
  const groups = await api('/api/v1/admin/groups');
  allGroups = groups.data || [];
  const container = $('#groupsCards');
  container.innerHTML = allGroups.map((g) => `
    <div class="col" data-group-id="${g.id}">
      <div class="card group-card h-100">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <h5 class="card-title mb-0">${esc(g.name)}</h5>
            <span class="badge ${g.is_admin ? 'text-bg-primary' : 'text-bg-secondary'}">${g.is_admin ? 'admin' : '普通'}</span>
          </div>
          <p class="card-text text-body-secondary small mb-1">ID: ${g.id}</p>
          <p class="card-text text-body-secondary small mb-0">配额限额: ${g.quota_limit === 0 ? '不限' : g.quota_limit}</p>
        </div>
        <div class="card-footer bg-transparent border-top-0 d-flex justify-content-end gap-2">
          <button type="button" class="btn btn-sm btn-outline-primary" data-action="edit-group">修改</button>
          <button type="button" class="btn btn-sm btn-outline-danger" data-action="delete-group">删除</button>
        </div>
      </div>
    </div>`).join('');
}

function startEditGroup(id) {
  const g = allGroups.find((x) => x.id === id);
  if (!g) return;
  $('#groupEditId').value = g.id;
  $('#groupName').value = g.name;
  $('#groupIsAdmin').value = g.is_admin ? '1' : '0';
  $('#groupQuota').value = g.quota_limit;
  $('#groupFormTitle').textContent = `编辑权限组 #${g.id}`;
  $('#saveGroup').textContent = '保存修改';
  $('#cancelEditGroup').classList.remove('d-none');
  $('#groupName').focus();
}

function cancelEditGroup() {
  $('#groupEditId').value = '';
  $('#groupName').value = '';
  $('#groupIsAdmin').value = '0';
  $('#groupQuota').value = '0';
  $('#groupFormTitle').textContent = '新建权限组';
  $('#saveGroup').textContent = '创建';
  $('#cancelEditGroup').classList.add('d-none');
}

async function saveGroup() {
  const editId = $('#groupEditId').value;
  const body = {
    name: $('#groupName').value.trim(),
    is_admin: $('#groupIsAdmin').value === '1',
    quota_limit: parseInt($('#groupQuota').value) || 0,
  };
  if (!body.name) {
    showMsg('groupMsg', { code: 0, msg: '名称不能为空' });
    return;
  }

  let r;
  if (editId) {
    r = await api(`/api/v1/admin/groups/${editId}`, { method: 'PUT', body: JSON.stringify(body) });
    if (r.code === 1) cancelEditGroup();
  } else {
    r = await api('/api/v1/admin/groups', { method: 'POST', body: JSON.stringify(body) });
    if (r.code === 1) {
      $('#groupName').value = '';
      $('#groupQuota').value = '0';
    }
  }
  showMsg('groupMsg', r);
  if (r.code === 1) loadGroups();
}

async function deleteGroup(id) {
  if (!confirm(`确认删除权限组 #${id}？\n若组内仍有用户或邀请码引用，删除将被拒绝。`)) return;
  const r = await api(`/api/v1/admin/groups/${id}`, { method: 'DELETE' });
  showMsg('groupMsg', r);
  loadGroups();
}

$('#saveGroup').addEventListener('click', saveGroup);
$('#cancelEditGroup').addEventListener('click', cancelEditGroup);

// ---- 邀请码 ----
async function loadInvites() {
  const [invites, groups] = await Promise.all([
    api('/api/v1/admin/invites'),
    api('/api/v1/admin/groups'),
  ]);
  const gmap = Object.fromEntries((groups.data || []).map((g) => [g.id, g]));

  const tbody = $('#invitesTable tbody');
  tbody.innerHTML = (invites.data || []).map((inv) => {
    const g = gmap[inv.group_id];
    return `
      <tr data-invite-code="${esc(inv.code)}">
        <td><span class="badge text-bg-warning font-monospace">${esc(inv.code)}</span></td>
        <td>${esc(g?.name || inv.group_id)}</td>
        <td>${inv.used_count}/${inv.max_uses}</td>
        <td class="text-body-secondary">${inv.expires_at ? esc(inv.expires_at) : '永久'}</td>
        <td class="text-body-secondary">${esc(inv.created_by_username || '-')}</td>
        <td class="text-body-secondary">${(inv.used_by_users || []).map((u) => esc(u)).join(', ') || '-'}</td>
        <td class="table-actions">
          <button type="button" class="btn btn-sm btn-outline-danger" data-action="delete-invite">删除</button>
        </td>
      </tr>`;
  }).join('');

  const gsel = $('#inviteGroup');
  gsel.innerHTML = (groups.data || []).map((g) => `<option value="${g.id}">${esc(g.name)}${g.is_admin ? ' (admin)' : ''}</option>`).join('');
}

async function deleteInvite(code) {
  if (!confirm(`确认删除邀请码 ${code}？`)) return;
  const r = await api(`/api/v1/admin/invites/${encodeURIComponent(code)}`, { method: 'DELETE' });
  showMsg('inviteMsg', r);
  loadInvites();
}

$('#createInvite').addEventListener('click', async () => {
  const body = {
    group_id: parseInt($('#inviteGroup').value),
    max_uses: parseInt($('#inviteUses').value) || 1,
  };
  const exp = $('#inviteExpires').value.trim();
  if (exp) body.expires_at = exp;
  const r = await api('/api/v1/admin/invites', { method: 'POST', body: JSON.stringify(body) });
  showMsg('inviteMsg', r);
  if (r.code === 1) loadInvites();
});

// ---- 事件委托（替代内联 onclick） ----
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  const row = btn.closest('tr') || btn.closest('[data-group-id]');

  if (action === 'edit-user' && row) {
    selectUser(parseInt(row.dataset.userId));
  } else if (action === 'delete-user' && row) {
    deleteUser(parseInt(row.dataset.userId));
  } else if (action === 'edit-group' && row) {
    startEditGroup(parseInt(row.dataset.groupId));
  } else if (action === 'delete-group' && row) {
    deleteGroup(parseInt(row.dataset.groupId));
  } else if (action === 'delete-invite' && row) {
    deleteInvite(row.dataset.inviteCode);
  }
});

// ---- 用量统计 ----
let statsDays = 1;
let hourlyChart = null;
let userChart = null;

function chartTheme() {
  // 直接读取 CSS 设计令牌，深浅主题切换时无需手写两套色值
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  return { tick: v('--ns-fg-1'), grid: v('--ns-line'), blue: v('--ns-accent'), orange: v('--ns-warning') };
}

async function loadStats() {
  const [overview, hourly, byUser] = await Promise.all([
    api('/api/v1/admin/stats/overview'),
    api(`/api/v1/admin/stats/hourly?days=${statsDays}`),
    api(`/api/v1/admin/stats/by_user?days=${statsDays}`),
  ]);
  renderStatsSummary(overview.data);
  renderHourlyChart(hourly.data);
  renderUserChart(byUser.data);
}

function renderStatsSummary(data) {
  const today = data?.today || {};
  const allTime = data?.all_time || {};
  const active = today.active_users || 0;
  const avg = active > 0 ? Math.round((today.total_tokens || 0) / active) : 0;
  const since = allTime.since ? new Date(allTime.since * 1000).toLocaleDateString() : '—';
  const card = (title, main, sub) => `
    <div class="col">
      <div class="card h-100">
        <div class="card-body">
          <h6 class="card-subtitle mb-2 text-body-secondary">${title}</h6>
          <p class="card-text fs-3 fw-semibold mb-0">${main}</p>
          <p class="card-text text-body-secondary small mb-0">${sub}</p>
        </div>
      </div>
    </div>`;
  $('#statsSummary').innerHTML =
    card('今日用量', fmt(today.total_tokens || 0), `输入 ${fmt(today.input_tokens || 0)} / 输出 ${fmt(today.output_tokens || 0)}`) +
    card('累计用量', fmt(allTime.total_tokens || 0), `自 ${since}`) +
    card('今日活跃用户', fmt(active), '今日有用量记录的用户数') +
    card('今日人均用量', fmt(avg), '今日总量 ÷ 活跃用户数');
}

function renderHourlyChart(data) {
  const points = data?.points || [];
  const hasData = points.some((p) => (p.total_tokens || 0) > 0);
  $('#statsHourlyEmpty').classList.toggle('d-none', hasData);
  if (hourlyChart) { hourlyChart.destroy(); hourlyChart = null; }
  if (!hasData || typeof Chart === 'undefined') return;

  const t = chartTheme();
  const labels = points.map((p) => {
    const d = new Date(p.ts * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    if (statsDays === 1) return `${hh}:00`;
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${mm}-${dd} ${hh}:00`;
  });
  hourlyChart = new Chart($('#statsHourlyChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '输入 tokens',
          data: points.map((p) => p.input_tokens || 0),
          borderColor: t.blue,
          backgroundColor: t.blue + '55',
          fill: true,
          borderWidth: 2,
          pointRadius: 0,
          pointHitRadius: 10,
          tension: 0.2,
        },
        {
          label: '输出 tokens',
          data: points.map((p) => p.output_tokens || 0),
          borderColor: t.orange,
          backgroundColor: t.orange + '55',
          fill: true,
          borderWidth: 2,
          pointRadius: 0,
          pointHitRadius: 10,
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, labels: { color: t.tick } },
        tooltip: {
          callbacks: { label: (c) => ` ${c.dataset.label}：${fmt(c.parsed.y)}` },
        },
      },
      scales: {
        x: { stacked: true, ticks: { color: t.tick, maxTicksLimit: 12 }, grid: { color: t.grid } },
        y: { stacked: true, beginAtZero: true, ticks: { color: t.tick }, grid: { color: t.grid } },
      },
    },
  });
}

function renderUserChart(data) {
  const users = data?.users || [];
  const tbody = $('#statsUserTable tbody');
  tbody.innerHTML = users.length
    ? users.map((u) => `
        <tr>
          <td>${esc(u.username || '#' + u.user_id)}</td>
          <td>${fmt(u.input_tokens || 0)}</td>
          <td>${fmt(u.output_tokens || 0)}</td>
          <td><b>${fmt(u.total_tokens || 0)}</b></td>
        </tr>`).join('')
    : '<tr><td colspan="4" class="text-body-secondary">所选范围暂无用量</td></tr>';

  if (userChart) { userChart.destroy(); userChart = null; }
  if (!users.length || typeof Chart === 'undefined') return;

  // 用户是开放集合：Top 10 单列展示，其余折叠为“其他”一根条
  const top = users.slice(0, 10);
  const rest = users.slice(10);
  const labels = top.map((u) => u.username || '#' + u.user_id);
  const values = top.map((u) => u.total_tokens || 0);
  if (rest.length) {
    labels.push('其他');
    values.push(rest.reduce((s, u) => s + (u.total_tokens || 0), 0));
  }

  const t = chartTheme();
  userChart = new Chart($('#statsUserChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: '合计 tokens',
        data: values,
        backgroundColor: t.blue,
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => ` 合计：${fmt(c.parsed.x)}` } },
      },
      scales: {
        x: { beginAtZero: true, ticks: { color: t.tick }, grid: { color: t.grid } },
        y: { ticks: { color: t.tick }, grid: { display: false } },
      },
    },
  });
}

$$('#statsRange [data-days]').forEach((btn) => {
  btn.addEventListener('click', () => {
    statsDays = parseInt(btn.dataset.days, 10) || 1;
    $$('#statsRange [data-days]').forEach((b) => {
      b.classList.toggle('btn-primary', b === btn);
      b.classList.toggle('btn-outline-primary', b !== btn);
    });
    loadStats();
  });
});

// ---- 配额 ----
async function loadQuota() {
  const [settings, usage] = await Promise.all([
    api('/api/v1/admin/quota/settings'),
    api('/api/v1/admin/quota/usage'),
  ]);
  if (settings.data) {
    $('#quotaWindow').value = settings.data.window || 'day';
    $('#globalLimit').value = settings.data.global_limit ?? 0;
  }
  if (usage.data) {
    const pool = usage.data.pool || {};
    const limit = settings.data?.global_limit ?? 0;
    const remaining = limit > 0 ? Math.max(0, limit - (pool.total_tokens || 0)) : null;
    $('#poolUsage').innerHTML = `
      <div>窗口：<b>${esc(usage.data.window)}</b>（起点 ${new Date((usage.data.window_start || 0) * 1000).toLocaleString()}）</div>
      <div>已用：<b>${pool.total_tokens || 0}</b>（输入 ${pool.input_tokens || 0} / 输出 ${pool.output_tokens || 0}）</div>
      <div>上限：<b>${limit > 0 ? limit : '不限'}</b>${remaining !== null ? `，剩余 <b>${remaining}</b>` : ''}</div>
    `;
    const tbody = $('#quotaUsageTable tbody');
    const rows = usage.data.users || [];
    tbody.innerHTML = rows.length
      ? rows.map((u) => `
          <tr>
            <td>${esc(u.username || '#' + u.user_id)}</td>
            <td>${u.input_tokens || 0}</td>
            <td>${u.output_tokens || 0}</td>
            <td><b>${u.total_tokens || 0}</b></td>
          </tr>`).join('')
      : '<tr><td colspan="4" class="text-body-secondary">当前窗口尚无用量</td></tr>';
  }
}

$('#saveQuota').addEventListener('click', async () => {
  const body = {
    window: $('#quotaWindow').value,
    global_limit: parseInt($('#globalLimit').value) || 0,
  };
  const r = await api('/api/v1/admin/quota/settings', { method: 'PUT', body: JSON.stringify(body) });
  showMsg('quotaMsg', r);
  if (r.code === 1) loadQuota();
});

$('#resetQuota').addEventListener('click', async () => {
  if (!confirm('确认清空当前窗口下的所有用户与全局用量？此操作不可撤销。')) return;
  const r = await api('/api/v1/admin/quota/reset', { method: 'POST' });
  showMsg('quotaMsg', r);
  if (r.code === 1) loadQuota();
});

// ---- 日志 ----
const OPLOG_PAGE_SIZE = 50;
let oplogOffset = 0;
let oplogTotal = 0;

function loadLogs() {
  loadOpLogs();
  loadDshLog();
}

function fmtTs(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

async function loadOpLogs() {
  const level = $('#oplogLevel').value;
  const keyword = $('#oplogKeyword').value.trim();
  const params = new URLSearchParams({ limit: OPLOG_PAGE_SIZE, offset: oplogOffset });
  if (level) params.set('level', level);
  if (keyword) params.set('keyword', keyword);

  const r = await api(`/api/v1/admin/oplogs?${params}`);
  const rows = r.data?.rows || [];
  oplogTotal = r.data?.total || 0;

  const tbody = $('#oplogTable tbody');
  tbody.innerHTML = rows.length
    ? rows.map((log) => `
        <tr>
          <td class="text-body-secondary small">${fmtTs(log.ts)}</td>
          <td><span class="oplog-level-${esc(log.level)} fw-semibold">${esc(log.level)}</span></td>
          <td>${esc(log.username || '-')}</td>
          <td><code>${esc(log.action)}</code></td>
          <td class="oplog-detail">${esc(log.detail || '')}</td>
          <td class="text-body-secondary small">${esc(log.ip || '-')}</td>
        </tr>`).join('')
    : '<tr><td colspan="6" class="text-body-secondary">暂无日志</td></tr>';

  $('#oplogTotal').textContent = `共 ${oplogTotal} 条 · 第 ${Math.floor(oplogOffset / OPLOG_PAGE_SIZE) + 1} 页`;
  $('#oplogPrev').disabled = oplogOffset <= 0;
  $('#oplogNext').disabled = oplogOffset + OPLOG_PAGE_SIZE >= oplogTotal;
}

$('#oplogSearch').addEventListener('click', () => { oplogOffset = 0; loadOpLogs(); });
$('#oplogRefresh').addEventListener('click', () => loadOpLogs());
$('#oplogKeyword').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { oplogOffset = 0; loadOpLogs(); }
});
$('#oplogLevel').addEventListener('change', () => { oplogOffset = 0; loadOpLogs(); });
$('#oplogPrev').addEventListener('click', () => {
  oplogOffset = Math.max(0, oplogOffset - OPLOG_PAGE_SIZE);
  loadOpLogs();
});
$('#oplogNext').addEventListener('click', () => {
  if (oplogOffset + OPLOG_PAGE_SIZE < oplogTotal) {
    oplogOffset += OPLOG_PAGE_SIZE;
    loadOpLogs();
  }
});

async function loadDshLog() {
  const lines = $('#dshLogLines').value;
  const view = $('#dshLogView');
  view.textContent = '加载中…';
  const r = await api(`/api/v1/admin/dsh/log?lines=${lines}`);
  const d = r.data || {};
  if (!d.exists) {
    $('#dshLogMeta').textContent = '';
    view.textContent = 'dsh.log 不存在（DSH 可能尚未启动过）';
    return;
  }
  $('#dshLogMeta').textContent = `文件大小 ${(d.size / 1024).toFixed(1)} KB · 显示尾部 ${d.lines?.length || 0} 行`;
  view.textContent = d.text || '（日志为空）';
  // 滚动到底部，看最新日志
  view.scrollTop = view.scrollHeight;
}

$('#dshLogRefresh').addEventListener('click', loadDshLog);
$('#dshLogLines').addEventListener('change', loadDshLog);

// ---- 登出 ----
$('#logoutBtn').addEventListener('click', async (e) => {
  e.preventDefault();
  await api('/api/v1/auth/logout', { method: 'POST' });
  location.href = '/login';
});

// ---- 工具 ----
function showMsg(elOrId, r) {
  const el = typeof elOrId === 'string' ? $(`#${elOrId}`) : elOrId;
  if (!el) return;
  el.textContent = r.msg || (r.code === 1 ? '成功' : '失败');
  el.className = `mt-2 ${r.code === 1 ? 'msg-ok' : 'msg-err'}`;
}

// 初始加载
loadOverview();

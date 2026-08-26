import { api, showToast, esc, initTheme, toggleTheme } from './common.js';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

initTheme();
$('#themeToggle').addEventListener('click', toggleTheme);

// ---- Tab 切换 ----
$$('#adminTabs .nav-link').forEach((btn) => {
  btn.addEventListener('click', () => {
    $$('#adminTabs .nav-link').forEach((b) => b.classList.remove('active'));
    $$('section.page').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    $(`#page-${btn.dataset.page}`).classList.add('active');
    switch (btn.dataset.page) {
      case 'overview': loadOverview(); break;
      case 'users': loadUsers(); break;
      case 'groups': loadGroups(); break;
      case 'invites': loadInvites(); break;
      case 'quota': loadQuota(); break;
    }
  });
});

// ---- 概览 ----
async function loadOverview() {
  const chk = await api('/api/v1/auth/check');
  if (chk.data && chk.data.username) {
    $('#currentUser').textContent = `当前用户：${esc(chk.data.username)}`;
  }

  const [users, dsh] = await Promise.all([
    api('/api/v1/admin/users'),
    api('/api/v1/admin/dsh/status'),
  ]);

  const list = users.data || [];
  const stats = $('#overviewStats');
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
  `;

  const statusEl = $('#dshStatus');
  if (dsh.data?.running) {
    statusEl.className = 'badge text-bg-success';
    statusEl.textContent = `运行中 (PID ${dsh.data.pid})`;
  } else {
    statusEl.className = 'badge text-bg-secondary';
    statusEl.textContent = '已停止';
  }
}

$('#dshStart').addEventListener('click', async () => {
  const r = await api('/api/v1/admin/dsh/start', { method: 'POST' });
  showMsg(r);
  loadOverview();
});
$('#dshStop').addEventListener('click', async () => {
  const r = await api('/api/v1/admin/dsh/stop', { method: 'POST' });
  showMsg(r);
  loadOverview();
});

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
        <td class="text-body-secondary">${inv.expires_at || '永久'}</td>
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

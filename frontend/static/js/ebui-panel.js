/**
 * NekoSeek EBUI 注入面板
 * - 右下角悬浮：当前用户名 / 限额 / 已用额度 / 退出登录
 * - 每 3 秒轮询 GET /api/v1/panel/me
 * - 语言跟随 DSH 页面：实测 DSH locale 插件切换语言时修改 <html lang>，
 *   用 MutationObserver 监听该属性即时切换文案，navigator.language 兜底。
 */
(function () {
  'use strict';

  if (window.__nekoseekPanelLoaded) return;
  window.__nekoseekPanelLoaded = true;

  var POLL_INTERVAL = 3000;

  var I18N = {
    'zh-CN': {
      quota: '限额',
      used: '已用',
      unlimited: '不限',
      logout: '退出登录',
      loggingOut: '正在退出…',
      brand: 'NekoSeek',
    },
    'en': {
      quota: 'Quota',
      used: 'Used',
      unlimited: 'Unlimited',
      logout: 'Log out',
      loggingOut: 'Logging out…',
      brand: 'NekoSeek',
    },
  };

  function normalizeLang(raw) {
    if (!raw) return null;
    var s = String(raw).toLowerCase().replace('_', '-');
    if (s === 'zh-tw' || s === 'zh-hk' || s === 'zh-hant' || s.indexOf('zh-hant') === 0) return 'zh-TW';
    if (s.indexOf('zh') === 0) return 'zh-CN';
    if (s.indexOf('en') === 0) return 'en';
    return null;
  }

  function detectLang() {
    // DSH locale 插件切换语言时直接修改 <html lang>（实测 zh-CN <-> en）
    return normalizeLang(document.documentElement.lang)
      || normalizeLang(navigator.language)
      || 'en';
  }

  var currentLang = detectLang();
  function t(key) {
    var dict = I18N[currentLang] || I18N['en'];
    return dict[key] || I18N['en'][key] || key;
  }

  // ---------- 面板 DOM ----------

  var panel = null;
  var usernameEl, quotaEl, usedEl, barFillEl, logoutBtn, quotaLabelEl, usedLabelEl;

  function buildPanel() {
    panel = document.createElement('div');
    panel.id = 'nekoseek-panel';

    var head = document.createElement('div');
    head.className = 'nsp-head';
    var brand = document.createElement('span');
    brand.className = 'nsp-brand';
    head.appendChild(brand);
    panel.appendChild(head);

    var body = document.createElement('div');
    body.className = 'nsp-body';

    var userRow = document.createElement('div');
    userRow.className = 'nsp-row nsp-user';
    usernameEl = document.createElement('b');
    userRow.appendChild(usernameEl);
    body.appendChild(userRow);

    var quotaRow = document.createElement('div');
    quotaRow.className = 'nsp-row';
    quotaLabelEl = document.createElement('span');
    quotaEl = document.createElement('b');
    quotaRow.appendChild(quotaLabelEl);
    quotaRow.appendChild(quotaEl);
    body.appendChild(quotaRow);

    var usedRow = document.createElement('div');
    usedRow.className = 'nsp-row';
    usedLabelEl = document.createElement('span');
    usedEl = document.createElement('b');
    usedRow.appendChild(usedLabelEl);
    usedRow.appendChild(usedEl);
    body.appendChild(usedRow);

    var bar = document.createElement('div');
    bar.className = 'nsp-bar';
    barFillEl = document.createElement('div');
    barFillEl.className = 'nsp-bar-fill';
    bar.appendChild(barFillEl);
    body.appendChild(bar);

    logoutBtn = document.createElement('button');
    logoutBtn.className = 'nsp-logout';
    logoutBtn.type = 'button';
    logoutBtn.addEventListener('click', onLogout);
    body.appendChild(logoutBtn);

    panel.appendChild(body);
    return panel;
  }

  function ensureMounted() {
    if (!panel) buildPanel();
    if (!panel.isConnected && document.body) {
      document.body.appendChild(panel);
    }
  }

  function applyI18n() {
    if (!panel) return;
    panel.querySelector('.nsp-brand').textContent = t('brand');
    quotaLabelEl.textContent = t('quota');
    usedLabelEl.textContent = t('used');
    logoutBtn.textContent = logoutBtn.disabled ? t('loggingOut') : t('logout');
  }

  function render(data) {
    ensureMounted();
    if (!panel.isConnected) return;

    usernameEl.textContent = data.username || '';

    var limit = data.quota_limit || 0;
    var used = data.used_quota || 0;

    if (limit > 0) {
      quotaEl.textContent = String(limit);
      usedEl.textContent = used + ' / ' + limit;
      var pct = Math.min(100, Math.round(used / limit * 100));
      barFillEl.style.width = pct + '%';
      barFillEl.classList.toggle('nsp-danger', pct >= 90);
    } else {
      quotaEl.textContent = t('unlimited');
      usedEl.textContent = String(used);
      barFillEl.style.width = '0%';
      barFillEl.classList.remove('nsp-danger');
    }
  }

  // ---------- 数据轮询 ----------

  var timer = null;

  function refresh() {
    fetch('/api/v1/panel/me', { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401) {
          // 会话失效，停止轮询
          if (timer) { clearInterval(timer); timer = null; }
          throw new Error('unauthorized');
        }
        if (!r.ok) throw new Error('http ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (d && d.code === 1 && d.data) render(d.data);
      })
      .catch(function () { /* 静默：网络抖动或未登录 */ });
  }

  // ---------- 退出登录 ----------

  function onLogout() {
    logoutBtn.disabled = true;
    logoutBtn.textContent = t('loggingOut');
    fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' })
      .catch(function () { /* 无论成败都跳转 */ })
      .finally(function () { location.href = '/login'; });
  }

  // ---------- 语言跟随 ----------

  var langObserver = new MutationObserver(function () {
    var lang = detectLang();
    if (lang !== currentLang) {
      currentLang = lang;
      applyI18n();
      // 限额为 0 时“不限”文案也随语言变化，触发一次重绘
      refresh();
    }
  });

  function start() {
    buildPanel();
    ensureMounted();
    applyI18n();
    langObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['lang'],
    });
    refresh();
    timer = setInterval(refresh, POLL_INTERVAL);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();

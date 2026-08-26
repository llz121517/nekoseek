/**
 * NekoSeek 前端共享工具模块
 * - 统一 API 请求与响应解析
 * - Bootstrap Toast 提示
 * - HTML 转义
 * - 深色/浅色主题切换
 */

const THEME_KEY = 'nekoseek-theme';

/**
 * 发起 API 请求，自动处理 JSON 与统一响应格式 {code, msg, data}。
 */
export async function api(path, opts = {}) {
  const options = {
    credentials: 'include',
    ...opts,
  };
  if (options.body && typeof options.body === 'string' && !options.headers?.['Content-Type']) {
    options.headers = {
      ...options.headers,
      'Content-Type': 'application/json',
    };
  }

  let res;
  try {
    res = await fetch(path, options);
  } catch (err) {
    return { code: 0, msg: `网络错误: ${err.message}` };
  }

  let data;
  try {
    data = await res.json();
  } catch {
    data = { code: 0, msg: `响应解析失败 (HTTP ${res.status})` };
  }

  if (data.code === undefined) {
    data = { code: res.ok ? 1 : 0, msg: data.msg || `HTTP ${res.status}` };
  }
  return data;
}

/**
 * 显示一个 Bootstrap Toast 提示。
 * @param {string} message
 * @param {'success'|'danger'|'warning'|'info'} type
 */
export function showToast(message, type = 'success') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    container.style.zIndex = '10800';
    document.body.appendChild(container);
  }

  const toastEl = document.createElement('div');
  toastEl.className = `toast align-items-center text-bg-${type} border-0`;
  toastEl.setAttribute('role', 'alert');
  toastEl.setAttribute('aria-live', 'assertive');
  toastEl.setAttribute('aria-atomic', 'true');
  toastEl.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${esc(message)}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
    </div>
  `;
  container.appendChild(toastEl);

  const toast = new bootstrap.Toast(toastEl, { delay: 2500 });
  toast.show();
  toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

/**
 * HTML 转义，防止 XSS。
 */
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[c]));
}

/**
 * 初始化主题：读取 localStorage 或跟随系统偏好。
 */
export function initTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  let theme = stored;
  if (!theme) {
    theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-bs-theme', theme);
  updateThemeIcon(theme);
  return theme;
}

/**
 * 切换主题并持久化。
 */
export function toggleTheme() {
  const current = document.documentElement.getAttribute('data-bs-theme') || 'light';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-bs-theme', next);
  localStorage.setItem(THEME_KEY, next);
  updateThemeIcon(next);
  return next;
}

function updateThemeIcon(theme) {
  document.querySelectorAll('[data-theme-icon]').forEach((el) => {
    el.innerHTML = theme === 'dark' ? moonSvg() : sunSvg();
    el.setAttribute('title', theme === 'dark' ? '切换为浅色主题' : '切换为深色主题');
  });
}

function sunSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16">
    <path d="M8 11a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm0 1a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM8 0a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-1 0v-2A.5.5 0 0 1 8 0zm0 13a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-1 0v-2A.5.5 0 0 1 8 13zm8-5a.5.5 0 0 1-.5.5h-2a.5.5 0 0 1 0-1h2a.5.5 0 0 1 .5.5zM3 8a.5.5 0 0 1-.5.5h-2a.5.5 0 0 1 0-1h2A.5.5 0 0 1 3 8zm10.657-5.657a.5.5 0 0 1 0 .707l-1.414 1.415a.5.5 0 1 1-.707-.708l1.414-1.414a.5.5 0 0 1 .707 0zm-9.193 9.193a.5.5 0 0 1 0 .707L3.05 13.657a.5.5 0 0 1-.707-.707l1.414-1.414a.5.5 0 0 1 .707 0zm9.193 2.121a.5.5 0 0 1-.707 0l-1.414-1.414a.5.5 0 0 1 .707-.707l1.414 1.414a.5.5 0 0 1 0 .707zM4.464 4.465a.5.5 0 0 1-.707 0L2.343 3.05a.5.5 0 1 1 .707-.707l1.414 1.414a.5.5 0 0 1 0 .708z"/>
  </svg>`;
}

function moonSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16">
    <path d="M6 .278a.768.768 0 0 1 .08.858 7.208 7.208 0 0 0-.878 3.46c0 4.021 3.278 7.277 7.318 7.277.527 0 1.04-.055 1.533-.16a.787.787 0 0 1 .81.316.733.733 0 0 1-.031.893A8.349 8.349 0 0 1 8.344 16C3.734 16 0 12.286 0 7.71 0 4.266 2.114 1.312 5.124.06A.752.752 0 0 1 6 .278z"/>
  </svg>`;
}

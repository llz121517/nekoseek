import { api, showToast, initTheme, toggleTheme } from './common.js';

const form = document.getElementById('authForm');
const formTitle = document.getElementById('formTitle');
const submitBtn = document.getElementById('submitBtn');
const toggleLink = document.getElementById('toggleLink');
const toggleText = document.getElementById('toggleText');
const inviteBox = document.getElementById('inviteBox');
const inviteInput = document.getElementById('invite_code');
const usernameInput = document.getElementById('username');
const passwordInput = document.getElementById('password');

let mode = 'login';

initTheme();
document.getElementById('themeToggle').addEventListener('click', toggleTheme);

toggleLink.addEventListener('click', (e) => {
  e.preventDefault();
  mode = mode === 'login' ? 'register' : 'login';
  if (mode === 'login') {
    formTitle.textContent = 'NekoSeek 登录';
    submitBtn.textContent = '立即登录';
    toggleText.textContent = '没有账号？';
    toggleLink.textContent = '注册';
    inviteBox.classList.add('d-none');
    inviteInput.disabled = true;
    inviteInput.required = false;
  } else {
    formTitle.textContent = 'NekoSeek 注册';
    submitBtn.textContent = '立即注册';
    toggleText.textContent = '已有账号？';
    toggleLink.textContent = '登录';
    inviteBox.classList.remove('d-none');
    inviteInput.disabled = false;
    inviteInput.required = true;
  }
  form.classList.remove('was-validated');
});

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  e.stopPropagation();

  if (!form.checkValidity()) {
    form.classList.add('was-validated');
    return;
  }

  const body = {
    username: usernameInput.value.trim(),
    password: passwordInput.value,
  };
  const endpoint = mode === 'login' ? '/api/v1/auth/login' : '/api/v1/auth/register';
  if (mode === 'register') {
    body.invite_code = inviteInput.value.trim();
  }

  setLoading(true);
  const res = await api(endpoint, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  setLoading(false);

  if (res.code === 1) {
    showToast((mode === 'login' ? '登录' : '注册') + '成功，页面即将跳转~', 'success');
    if (mode === 'login') {
      setTimeout(async () => {
        const chk = await api('/api/v1/auth/check');
        location.href = (chk.data && chk.data.is_admin) ? '/admin' : '/';
      }, 800);
    } else {
      setTimeout(() => {
        toggleLink.click();
        passwordInput.value = '';
      }, 800);
    }
  } else {
    showToast('失败：' + (res.msg || '未知错误'), 'danger');
    passwordInput.value = '';
  }
});

function setLoading(loading) {
  submitBtn.disabled = loading;
  if (loading) {
    submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>
      <span class="visually-hidden" role="status">Loading...</span>
      ${mode === 'login' ? '登录中...' : '注册中...'}`;
  } else {
    submitBtn.textContent = mode === 'login' ? '立即登录' : '立即注册';
  }
}

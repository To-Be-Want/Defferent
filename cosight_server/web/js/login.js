(() => {
  let registering = false;
  let mode = 'closed';
  let configured = false;
  let submitting = false;
  const form = document.getElementById('account-form');
  const submit = document.getElementById('submit');
  const error = document.getElementById('account-error');
  const password = document.getElementById('password');
  const confirm = document.getElementById('confirm-password');
  const invitation = document.getElementById('invitation');
  function setMode(value) {
    if (submitting) return;
    if (value && (!configured || mode === 'closed')) {
      error.textContent = configured ? '当前未开放注册，请联系管理员。' : '正在读取注册方式，请稍候。';
      return;
    }
    registering = value;
    document.getElementById('login-tab').setAttribute('aria-selected', String(!registering));
    document.getElementById('register-tab').setAttribute('aria-selected', String(registering));
    document.getElementById('form-title').textContent = registering ? '创建你的学习账号' : '欢迎回来';
    document.getElementById('form-description').textContent = registering ? '从这里开始，建立属于你的学习空间。' : '登录账号，继续你的复习计划。';
    form.setAttribute('aria-label', registering ? '注册表单' : '登录表单');
    document.getElementById('confirm-password-group').hidden = !registering;
    confirm.disabled = !registering;
    confirm.required = registering;
    const needsInvitation = registering && mode === 'invite';
    document.getElementById('invitation-label').hidden = !needsInvitation;
    invitation.disabled = !needsInvitation;
    invitation.required = needsInvitation;
    document.getElementById('username-hint').hidden = !registering;
    document.getElementById('password-help').hidden = registering;
    password.autocomplete = registering ? 'new-password' : 'current-password';
    submit.textContent = registering ? '注册并进入学习空间' : '登录';
    document.getElementById('switch-prompt-text').textContent = registering ? '已经有账号？' : '还没有账号？';
    document.getElementById('switch-mode').textContent = registering ? '立即登录' : '立即注册';
    error.textContent = '';
  }
  document.getElementById('login-tab').onclick = () => setMode(false);
  document.getElementById('register-tab').onclick = () => setMode(true);
  document.getElementById('switch-mode').onclick = () => setMode(!registering);
  document.getElementById('toggle-password').onclick = event => {
    const visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    event.currentTarget.textContent = visible ? '隐藏' : '显示';
    event.currentTarget.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    event.currentTarget.setAttribute('aria-pressed', String(visible));
  };
  const dialog = document.getElementById('password-help-dialog');
  document.getElementById('password-help').onclick = () => dialog.showModal();
  document.getElementById('close-password-help').onclick = () => dialog.close();
  fetch('/auth/config', {cache:'no-store'}).then(async response => {
    if (!response.ok) throw new Error('无法读取注册方式');
    mode = (await response.json()).registration_mode;
    configured = true;
    document.getElementById('registration-notice').textContent = mode === 'invite' ? '当前为邀请注册，请向管理员获取邀请码。' : mode === 'closed' ? '当前未开放注册，请使用已有账号或联系管理员。' : '可直接注册，无需邀请码。请勿使用其他重要账户的相同密码。';
    document.getElementById('register-tab').hidden = mode === 'closed';
    document.getElementById('switch-mode').hidden = mode === 'closed';
    document.getElementById('switch-prompt-text').hidden = mode === 'closed';
  }).catch(() => { document.getElementById('registration-notice').textContent = '暂时无法读取注册方式，已有账号仍可尝试登录。'; });
  form.onsubmit = async event => {
    event.preventDefault();
    if (submitting) return;
    error.textContent = '';
    if (registering && password.value !== confirm.value) {
      error.textContent = '两次输入的密码不一致，请重新确认。';
      confirm.focus();
      return;
    }
    submitting = true;
    submit.disabled = true;
    submit.textContent = registering ? '正在创建账号…' : '正在登录…';
    try {
      const response = await fetch(registering ? '/auth/register' : '/auth/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: document.getElementById('username').value.trim(),
          password: password.value,
          invitation_code: registering && mode === 'invite' ? invitation.value : ''})});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查账号、密码和邀请码');
      // Verify the browser really accepted the session cookie before redirecting.
      const check = await fetch('/auth/me', {cache: 'no-store'});
      if (!check.ok) throw new Error('无法保持登录。请使用HTTPS；本地HTTP测试请按部署说明配置Cookie。');
      Object.keys(localStorage).filter(key => key.startsWith('cosight:')).forEach(key => localStorage.removeItem(key));
      localStorage.setItem('cosight:accountId', data.user.id);
      const next = new URL(new URLSearchParams(location.search).get('next') || '/cosight/', location.origin);
      location.replace(next.origin === location.origin && (next.pathname.startsWith('/cosight/') || next.pathname.startsWith('/quiz/')) ? next.href : '/cosight/');
    } catch (failure) { error.textContent = failure.message || '连接失败，请稍后重试'; }
    finally {
      submitting = false;
      submit.disabled = false;
      submit.textContent = registering ? '注册并进入学习空间' : '登录';
    }
  };
})();

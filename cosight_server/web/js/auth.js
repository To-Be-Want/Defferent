/* Same-origin session authentication; credentials stay in an HttpOnly cookie. */
(() => {
  document.documentElement.classList.add('auth-pending');
  const nativeFetch = window.fetch.bind(window);
  let csrfToken = '';
  const clearAccountCache = () => {
    Object.keys(localStorage).filter(key => key.startsWith('cosight:')).forEach(key => localStorage.removeItem(key));
  };
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = String(options.method || input?.method || 'GET').toUpperCase();
    if (url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(options.headers || input?.headers || {});
      headers.set('X-CSRF-Token', csrfToken);
      options = {...options, headers};
    }
    const response = await nativeFetch(input, options);
    if (url.origin === location.origin && response.status === 401 && url.pathname !== '/auth/login') {
      clearAccountCache();
      location.replace('/cosight/login.html?next=' + encodeURIComponent(location.pathname));
    }
    return response;
  };
  window.authReady = nativeFetch('/auth/me', {cache: 'no-store'}).then(async response => {
    if (!response.ok) {
      clearAccountCache();
      location.replace('/cosight/login.html?next=' + encodeURIComponent(location.pathname));
      return null;
    }
    const data = await response.json();
    csrfToken = data.csrf_token;
    const previous = localStorage.getItem('cosight:accountId');
    if (previous !== data.user.id) {
      const hasOldCache = Object.keys(localStorage).some(key => key.startsWith('cosight:'));
      clearAccountCache();
      localStorage.setItem('cosight:accountId', data.user.id);
      if (hasOldCache) { location.reload(); return null; }
    }
    localStorage.setItem('cosight:accountId', data.user.id);
    window.currentAccount = data.user;
    document.documentElement.classList.remove('auth-pending');
    return data.user;
  }).catch(() => {
    document.documentElement.classList.remove('auth-pending');
    document.body?.insertAdjacentText('afterbegin', '暂时无法连接服务器，请刷新重试。');
    return null;
  });
  window.logoutAccount = async () => {
    const response = await window.fetch('/auth/logout', {method: 'POST'});
    if (!response.ok) throw new Error('退出失败，请重试');
    window.WebSocketService?.destroy();
    clearAccountCache();
    location.replace('/cosight/login.html');
  };
  window.safeMarkdown = text => {
    if (window.renderStudyMarkdown) return window.renderStudyMarkdown(text);
    const raw = typeof marked !== 'undefined' ? marked.parse(String(text)) : String(text);
    if (window.DOMPurify) return DOMPurify.sanitize(raw, {USE_PROFILES: {html: true},
      FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed', 'img', 'audio', 'video', 'source'],
      FORBID_ATTR: ['style', 'srcset'], ALLOW_DATA_ATTR: false});
    const element = document.createElement('div');
    element.textContent = String(text);
    return element.innerHTML; // Fail closed if the sanitizer cannot load.
  };
})();

document.addEventListener('DOMContentLoaded', async () => {
  const user = await window.authReady;
  if (!user) return;
  document.getElementById('notebook-user').textContent = user.username;
  const api = '/api/nae-deep-research/v1/deep-research/mistakes';
  const list = document.getElementById('mistake-list');
  const error = document.getElementById('notebook-error');
  const result = document.getElementById('notebook-result');
  const subject = document.getElementById('subject-filter');
  const status = document.getElementById('status-filter');
  const search = document.getElementById('mistake-search');
  const previous = document.getElementById('previous-page');
  const next = document.getElementById('next-page');
  const dialog = document.getElementById('add-mistake-dialog');
  const form = document.getElementById('add-mistake-form');
  let page = 1;
  let generation = 0;
  let controller;

  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const request = async (url, options) => {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请重试');
    return data;
  };
  const write = (url, method, body) => request(url, {
    method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });

  function makeCard(item) {
    const card = el('article', undefined, 'mistake-card');
    card.dataset.mistakeId = String(item.id);
    const meta = el('div', undefined, 'mistake-meta');
    meta.append(el('span', item.subject?.trim() || '未分类', 'mistake-subject'),
      el('span', item.mastered ? '已掌握' : '待复习', item.mastered ? 'mistake-mastered' : ''),
      el('span', `重做 ${item.review_count || 0} 次 · 连续自评正确 ${item.consecutive_correct || 0} 次`));
    card.append(meta, el('h2', item.question, 'mistake-question'));
    if (item.knowledge_points) card.append(el('p', `知识点：${item.knowledge_points}`, 'mistake-answer'));
    const details = el('details');
    details.append(el('summary', '我已作答，展开参考答案与解析'));
    const answer = el('div', undefined, 'mistake-answer');
    answer.append(el('strong', '参考答案'), el('div', item.correct_answer));
    if (item.explanation) answer.append(el('strong', '解析 / 备注'), el('div', item.explanation));
    details.append(answer);
    const reviews = el('div', undefined, 'mistake-actions');
    [true, false].forEach(correct => {
      const button = el('button', correct ? '自评：答对了' : '自评：还没掌握');
      button.type = 'button';
      button.dataset.reviewResult = String(correct);
      button.onclick = async () => {
        reviews.querySelectorAll('button').forEach(node => { node.disabled = true; });
        try {
          await write(`${api}/${item.id}/reviews`, 'POST', {is_correct: correct});
          await load(correct ? '已记录本次自评。连续两次正确会标记为已掌握。' : '已记录本次自评，题目已恢复为待复习。');
        } catch (failure) {
          error.textContent = failure.message;
          reviews.querySelectorAll('button').forEach(node => { node.disabled = false; });
        }
      };
      reviews.append(button);
    });
    details.append(reviews);
    card.append(details);
    const actions = el('div', undefined, 'mistake-actions');
    const toggle = el('button', item.mastered ? '恢复为待复习' : '手动标记已掌握');
    toggle.type = 'button';
    toggle.dataset.toggleMastery = '';
    toggle.onclick = async () => {
      toggle.disabled = true;
      try {
        await write(`${api}/${item.id}`, 'PATCH', {mastered: !item.mastered});
        await load(item.mastered ? '已恢复为待复习。' : '已手动标记掌握；这不是一次自动判分。');
      } catch (failure) { error.textContent = failure.message; toggle.disabled = false; }
    };
    actions.append(toggle);
    card.append(actions);
    return card;
  }

  async function load(message = '') {
    const mine = ++generation;
    controller?.abort();
    controller = new AbortController();
    list.setAttribute('aria-busy', 'true');
    error.textContent = '';
    result.textContent = '正在读取错题…';
    previous.disabled = true;
    next.disabled = true;
    try {
      const params = new URLSearchParams({subject: subject.value, status: status.value, q: search.value.trim(), page: String(page)});
      const data = await request(`${api}?${params}`, {signal: controller.signal});
      if (mine !== generation) return;
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      if (page > pages) { page = pages; return load(message); }
      document.getElementById('total-count').textContent = data.summary.total;
      document.getElementById('pending-count').textContent = data.summary.pending;
      document.getElementById('mastered-count').textContent = data.summary.mastered;
      const selected = subject.value;
      subject.replaceChildren(new Option('全部课程', ''));
      data.subjects.forEach(group => subject.add(new Option(`${group.name}（${group.count}）`, group.name)));
      if (selected && !data.subjects.some(group => group.name === selected)) subject.add(new Option(selected, selected));
      subject.value = selected;
      list.replaceChildren();
      if (!data.items.length) {
        list.append(el('div', data.summary.total ? '没有符合筛选条件的错题，试试切换课程、状态或关键词。' : '错题本还是空的。点击“手动录入错题”即可添加；聊天中保存的错题也会显示在这里。', 'notebook-empty'));
      } else data.items.forEach(item => list.append(makeCard(item)));
      result.textContent = `${message ? message + ' ' : ''}当前筛选共 ${data.total} 道错题。`;
      document.getElementById('page-label').textContent = `${page} / ${pages}`;
      previous.disabled = page <= 1;
      next.disabled = page >= pages;
    } catch (failure) {
      if (failure.name === 'AbortError' || mine !== generation) return;
      error.textContent = failure.message;
      result.textContent = '读取失败。请重新筛选或刷新页面重试。';
    } finally {
      if (mine === generation) list.setAttribute('aria-busy', 'false');
    }
  }
  document.getElementById('mistake-filters').onsubmit = event => { event.preventDefault(); page = 1; load(); };
  subject.onchange = status.onchange = () => { page = 1; load(); };
  previous.onclick = () => { page--; load(); };
  next.onclick = () => { page++; load(); };
  document.getElementById('add-mistake').onclick = () => {
    form.reset();
    document.getElementById('add-mistake-error').textContent = '';
    document.getElementById('new-mistake-subject').value = subject.value;
    dialog.showModal();
  };
  document.getElementById('cancel-add-mistake').onclick = () => dialog.close();
  form.onsubmit = async event => {
    event.preventDefault();
    const save = document.getElementById('save-new-mistake');
    save.disabled = true;
    const formError = document.getElementById('add-mistake-error');
    formError.textContent = '';
    try {
      await write(api, 'POST', Object.fromEntries(new FormData(form)));
      dialog.close();
      subject.value = '';
      status.value = 'all';
      search.value = '';
      page = 1;
      await load('错题已保存。');
    } catch (failure) { formError.textContent = failure.message; }
    finally { save.disabled = false; }
  };
  await load();
});

/* Passed dates require confirmation; completion is an explicit user choice. */
window.openExamSchedule = async function () {
  if (document.getElementById('study-schedule-dialog')) return;
  const endpoint = '/api/nae-deep-research/v1/deep-research/study-schedule';
  try {
    const response = await fetch(endpoint);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '读取考试排期失败');
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    const dialog = document.createElement('dialog');
    dialog.id = 'study-schedule-dialog';
    dialog.className = 'exam-schedule-dialog';
    dialog.setAttribute('aria-labelledby', 'exam-schedule-title');
    dialog.innerHTML = `<form id="exam-schedule-form">
      <header class="exam-dialog-header"><div><h2 id="exam-schedule-title">考试排期</h2><p>管理考试日期与状态，设置每天的复习时间。</p></div><button type="button" id="schedule-close" aria-label="关闭考试排期">×</button></header>
      <section><div class="exam-section-heading"><h3>待考试与待确认</h3><button type="button" id="schedule-add-exam">＋ 添加考试</button></div>
      <p class="exam-help">日期已过的考试请确认已考完，或更新延期日期。已考完课程不再自动排期，资料与错题继续保留。</p>
      <div id="schedule-subjects"></div><p id="schedule-empty" class="exam-help">还没有待考试，点击“添加考试”即可开始。</p>
      <details id="schedule-completed"><summary>已考完 <span id="schedule-completed-count"></span></summary><div id="schedule-completed-list"></div></details></section>
      <section><h3>每天复习时间段</h3><div id="schedule-time-slots"></div><button type="button" id="schedule-add-slot">＋ 添加时间段</button><p id="schedule-total" class="exam-help" aria-live="polite"></p></section>
      <p class="exam-help"><a href="/cosight/plans.html">查看完整计划、学习日历或添加个人事项 →</a></p>
      <p id="schedule-error" role="alert"></p><footer class="exam-dialog-actions"><button type="button" id="schedule-cancel">取消</button><button type="submit" id="schedule-save" class="exam-primary">保存并更新排期</button></footer>
    </form>`;
    document.body.appendChild(dialog);
    const active = dialog.querySelector('#schedule-subjects');
    const completed = dialog.querySelector('#schedule-completed-list');
    const updateCounts = () => {
      dialog.querySelector('#schedule-empty').hidden = active.children.length > 0;
      dialog.querySelector('#schedule-completed').hidden = completed.children.length === 0;
      dialog.querySelector('#schedule-completed-count').textContent = `（${completed.children.length}）`;
    };
    const addExam = (subject = {}) => {
      const row = document.createElement('div');
      row.className = 'schedule-exam-row';
      if (subject.id) row.dataset.subjectId = subject.id;
      row.dataset.examStatus = subject.exam_status || 'pending';
      row.innerHTML = `<div class="exam-row-name"><span class="exam-name"></span><span class="exam-badge"></span></div>
        <div class="exam-row-controls"><input class="schedule-exam-date" type="date" aria-label="考试日期" required><button type="button" class="exam-toggle"></button></div>`;
      const name = row.querySelector('.exam-name');
      if (subject.id) name.textContent = subject.name;
      else {
        const input = document.createElement('input');
        input.className = 'schedule-exam-name'; input.placeholder = '课程名称';
        input.setAttribute('aria-label', '课程名称'); input.maxLength = 40; input.required = true;
        name.appendChild(input);
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'exam-remove'; remove.textContent = '移除';
        remove.onclick = () => { row.remove(); updateCounts(); };
        row.querySelector('.exam-row-controls').appendChild(remove);
      }
      const date = row.querySelector('.schedule-exam-date');
      date.value = subject.exam_date || '';
      const sync = () => {
        const done = row.dataset.examStatus === 'completed';
        const overdue = !done && date.value && date.value < today;
        row.querySelector('.exam-badge').textContent = done ? '已考完' : !date.value ? '尚未设置考试日期' : overdue ? '日期已过 · 待确认' : '待考试';
        row.querySelector('.exam-badge').dataset.status = done ? 'completed' : overdue ? 'overdue' : 'pending';
        row.querySelector('.exam-toggle').textContent = done ? '恢复待考' : '标记已考完';
        row.querySelector('.exam-toggle').setAttribute('aria-label', `${done ? '恢复待考' : '标记已考完'}${subject.name || ''}`);
        const parent = done ? completed : active;
        if (row.parentElement !== parent) parent.appendChild(row);
        updateCounts();
      };
      row.querySelector('.exam-toggle').onclick = () => {
        if (!date.reportValidity()) return;
        const newName = row.querySelector('.schedule-exam-name');
        if (newName && !newName.reportValidity()) return;
        row.dataset.examStatus = row.dataset.examStatus === 'completed' ? 'pending' : 'completed';
        sync();
      };
      date.onchange = sync;
      sync();
    };
    (data.subjects || []).forEach(addExam);
    updateCounts();
    dialog.querySelector('#schedule-add-exam').onclick = () => {
      addExam(); active.lastElementChild.querySelector('.schedule-exam-name').focus();
    };
    const slots = dialog.querySelector('#schedule-time-slots');
    const updateTotal = () => {
      let total = 0;
      slots.querySelectorAll('.schedule-slot-row').forEach(row => {
        const minutes = value => { const [h,m] = value.split(':').map(Number); return h*60+m; };
        total += Math.max(0, minutes(row.querySelector('.schedule-end').value) - minutes(row.querySelector('.schedule-start').value)) || 0;
      });
      dialog.querySelector('#schedule-total').textContent = `每天可用复习时间：${Math.floor(total/60)} 小时 ${total%60} 分钟（${total} 分钟）`;
    };
    const addSlot = (slot = {start:'19:00', end:'21:00'}) => {
      const row = document.createElement('div'); row.className = 'schedule-slot-row';
      row.innerHTML = `<input class="schedule-start" type="time" aria-label="复习开始时间" required><span>至</span><input class="schedule-end" type="time" aria-label="复习结束时间" required><button type="button" aria-label="删除时间段">×</button>`;
      row.querySelector('.schedule-start').value = slot.start;
      row.querySelector('.schedule-end').value = slot.end;
      row.querySelector('button').onclick = () => {
        if (slots.children.length <= 1) { dialog.querySelector('#schedule-error').textContent = '每天至少保留一个复习时间段。'; return; }
        row.remove(); updateTotal();
      };
      row.onchange = updateTotal;
      slots.appendChild(row); updateTotal();
    };
    (data.study_time_slots || [{start:'19:00',end:'23:00'}]).forEach(addSlot);
    dialog.querySelector('#schedule-add-slot').onclick = () => addSlot();
    dialog.addEventListener('close', () => dialog.remove());
    ['schedule-cancel','schedule-close'].forEach(id => { dialog.querySelector('#'+id).onclick = () => dialog.close(); });
    dialog.querySelector('form').onsubmit = async event => {
      event.preventDefault();
      const button = dialog.querySelector('#schedule-save');
      const error = dialog.querySelector('#schedule-error');
      button.disabled = true; button.textContent = '正在保存…'; error.textContent = '';
      try {
        const subjects = [...dialog.querySelectorAll('.schedule-exam-row')].map(row => ({
          ...(row.dataset.subjectId ? {id:Number(row.dataset.subjectId)} : {name:row.querySelector('.schedule-exam-name').value.trim()}),
          exam_date:row.querySelector('.schedule-exam-date').value, exam_status:row.dataset.examStatus,
        }));
        const study_time_slots = [...slots.children].map(row => ({start:row.querySelector('.schedule-start').value,end:row.querySelector('.schedule-end').value}));
        const saved = await fetch(endpoint, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({subjects,study_time_slots})});
        const result = await saved.json();
        if (!saved.ok) throw new Error(result.detail || '保存失败');
        dialog.close();
        await window.refreshStudyDashboard?.();
        alert(result.replan_message || '考试排期已保存');
      } catch (failure) { error.textContent = failure.message || '保存失败，请重试'; }
      finally { button.disabled = false; button.textContent = '保存并更新排期'; }
    };
    dialog.showModal();
  } catch (error) { document.getElementById('study-schedule-dialog')?.remove(); alert(error.message || '无法打开考试排期'); }
};

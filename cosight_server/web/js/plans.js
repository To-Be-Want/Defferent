document.addEventListener('DOMContentLoaded', async () => {
  if (!await window.authReady) return;
  const base = '/api/nae-deep-research/v1/deep-research';
  const $ = id => document.getElementById(id);
  const iso = day => `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
  const today = iso(new Date());
  let selected = today, month = new Date(new Date().getFullYear(),new Date().getMonth(),1);
  let data = {tasks:[],subjects:[],summary:{}}, editing = null, loading = 0;
  $('plans-user').textContent = window.currentAccount.username;
  const make = (tag, className='', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const request = async (path, options={}) => {
    const response = await fetch(base+path, options);
    const result = await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(result.detail || '操作失败，请重试');
    return result;
  };
  const filtered = () => {
    const subject=$('plans-subject').value, status=$('plans-status').value, text=$('plans-search').value.trim().toLowerCase();
    return data.tasks.filter(task => (subject==='all' || (subject==='manual' ? task.source==='manual' : String(task.subject_id)===subject)) &&
      (status==='all' || (status==='待办' ? task.status!=='完成' : task.status==='完成')) &&
      (!text || `${task.title} ${task.subject_name} ${task.notes}`.toLowerCase().includes(text)));
  };
  const exams = () => data.subjects.filter(subject => $('plans-subject').value==='all' || String(subject.id)===$('plans-subject').value);
  const dateLabel = value => new Date(value+'T12:00:00').toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'short'});
  const endpoint = task => `/${task.source==='manual'?'manual-tasks':'tasks'}/${task.id}`;
  const showError = error => { $('plans-error').textContent = error.message || '加载失败，请重试'; };

  function showEditor(task=null) {
    editing = task;
    $('personal-task-form').reset();
    $('personal-task-title').textContent = task ? '编辑个人事项' : '添加个人事项';
    $('personal-title').value = task?.title || '';
    $('personal-date').value = task?.date || selected;
    $('personal-minutes').value = task?.est_minutes ?? 30;
    $('personal-priority').value = task?.priority || 'normal';
    $('personal-notes').value = task?.notes || '';
    $('personal-error').textContent='';
    $('personal-task-dialog').showModal();
  }

  function taskNode(task) {
    const article=make('article','plan-task'+(task.status==='完成'?' is-complete':''));
    article.dataset.taskKey=task.key;
    const head=make('div','plan-task-head');
    const checkbox=make('input'); checkbox.type='checkbox'; checkbox.checked=task.status==='完成';
    checkbox.setAttribute('aria-label',`${task.status==='完成'?'撤销完成':'标记完成'}：${task.title}`);
    checkbox.onchange=async()=>{
      checkbox.disabled=true;
      try {
        await request(endpoint(task),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:checkbox.checked?'完成':'待办'})});
        await load();
      } catch(error) { checkbox.checked=task.status==='完成'; showError(error); }
      finally { checkbox.disabled=false; }
    };
    const main=make('div','plan-task-main');
    main.appendChild(make('div','plan-task-title',task.title));
    const meta=make('div','plan-task-meta');
    meta.append(make('span','',task.source==='manual'?'个人事项':task.subject_name),make('span','',`${task.est_minutes} 分钟（预计）`));
    if (task.source==='manual') {
      const priority=make('span','priority-label',({high:'重要',normal:'普通',low:'较低'})[task.priority]);
      priority.dataset.priority=task.priority; meta.appendChild(priority);
    }
    meta.appendChild(make('span','',task.status==='完成'?'已完成':task.date<today?'已逾期 · 未完成':'未完成'));
    main.appendChild(meta);
    if(task.notes) main.appendChild(make('p','plan-task-notes',task.notes));
    head.append(checkbox,main); article.appendChild(head);
    if(task.source==='manual') {
      const actions=make('div','plan-task-actions');
      const edit=make('button','','编辑'); edit.type='button'; edit.onclick=()=>showEditor(task);
      const remove=make('button','','删除'); remove.type='button';
      remove.onclick=async()=>{
        if (!confirm(`删除事项“${task.title}”？`)) return;
        remove.disabled=true;
        try { await request(endpoint(task),{method:'DELETE'}); await load(); }
        catch(error) { showError(error); remove.disabled=false; }
      };
      actions.append(edit,remove); article.appendChild(actions);
    }
    return article;
  }

  function render() {
    const tasks=filtered();
    $('plans-total').textContent=data.summary.total ?? 0;
    $('plans-done').textContent=data.summary.completed ?? 0;
    $('plans-time').textContent=data.summary.total_minutes ?? 0;
    $('plans-range').textContent=data.start_date ? `${data.start_date} 至 ${data.end_date}` : '尚未安排任务或考试';
    $('month-title').textContent=`${month.getFullYear()}年${month.getMonth()+1}月`;
    const days=$('calendar-days'); days.replaceChildren();
    const offset=(month.getDay()+6)%7;
    for(let i=0;i<offset;i++) days.appendChild(make('span'));
    const count=new Date(month.getFullYear(),month.getMonth()+1,0).getDate();
    for(let day=1;day<=count;day++) {
      const value=iso(new Date(month.getFullYear(),month.getMonth(),day));
      const items=tasks.filter(task=>task.date===value), exam=exams().filter(subject=>subject.exam_date===value);
      const button=make('button',value===today?'is-today':''); button.type='button';
      button.dataset.date=value; button.setAttribute('aria-pressed',String(selected===value));
      button.setAttribute('aria-label',`${value}，${items.length}项任务，${exam.length}门考试`);
      button.appendChild(make('span','',day));
      if(items.length) {
        button.appendChild(make('span','task-dot'));
        button.appendChild(make('small','',`${items.filter(task=>task.status==='完成').length}/${items.length}`));
      }
      if(exam.length) button.appendChild(make('span','exam-day','考试'));
      button.onclick=()=>{ selected=value; render(); };
      days.appendChild(button);
    }
    $('selected-date').textContent=dateLabel(selected)+(selected===today?' · 今天':'');
    const daily=tasks.filter(task=>task.date===selected);
    $('day-summary').textContent=`已完成 ${daily.filter(task=>task.status==='完成').length}/${daily.length} 项 · 预计 ${daily.reduce((sum,task)=>sum+task.est_minutes,0)} 分钟`;
    $('day-exams').replaceChildren(...exams().filter(subject=>subject.exam_date===selected).map(subject=>make('div','day-exam',`${subject.name} · ${subject.display_status==='completed'?'已考完':subject.display_status==='overdue'?'考试日期已过，待确认':'考试日'}`)));
    $('day-tasks').replaceChildren(...(daily.length?daily.map(taskNode):[make('p','planner-empty','这一天没有符合筛选条件的任务，可以点击“添加事项”。')]));
    $('list-summary').textContent=`当前筛选共 ${tasks.length} 项，按日期显示整个期间；不会只显示今天。`;
    const list=$('all-plan-tasks'); list.replaceChildren();
    const groups=new Map();
    tasks.forEach(task=>{ if(!groups.has(task.date)) groups.set(task.date,[]); groups.get(task.date).push(task); });
    groups.forEach((items,day)=>{
      const section=make('section','plan-date-group'); section.appendChild(make('h3','',dateLabel(day)));
      items.forEach(task=>section.appendChild(taskNode(task))); list.appendChild(section);
    });
    if(!tasks.length) list.appendChild(make('p','planner-empty','还没有符合条件的任务。可以清除筛选、添加个人事项，或在学习主页生成复习计划。'));
  }

  async function load() {
    const serial=++loading;
    try {
      const result=await request('/plan-overview?include_archived='+$('include-archived').checked);
      if(serial!==loading) return;
      data=result; $('plans-error').textContent='';
      const select=$('plans-subject'), old=select.value;
      select.replaceChildren();
      [['all','全部任务'],['manual','个人事项'],...data.subjects.map(subject=>[String(subject.id),subject.name])].forEach(([value,title])=>{
        const option=make('option','',title); option.value=value; select.appendChild(option);
      });
      select.value=[...select.options].some(option=>option.value===old)?old:'all';
      render();
    } catch(error) { showError(error); }
  }

  $('add-personal-task').onclick=()=>showEditor(); $('day-add-task').onclick=()=>showEditor();
  $('personal-cancel').onclick=()=>$('personal-task-dialog').close();
  $('personal-task-form').onsubmit=async event=>{
    event.preventDefault(); const save=$('personal-save'); save.disabled=true;
    try {
      const body={title:$('personal-title').value.trim(),date:$('personal-date').value,est_minutes:Number($('personal-minutes').value),priority:$('personal-priority').value,notes:$('personal-notes').value};
      await request('/manual-tasks'+(editing?'/'+editing.id:''),{method:editing?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      selected=body.date; month=new Date(selected+'T12:00:00'); month.setDate(1);
      $('plans-subject').value='all'; $('plans-status').value='all'; $('plans-search').value='';
      $('personal-task-dialog').close(); await load();
    } catch(error) { $('personal-error').textContent=error.message; }
    finally { save.disabled=false; }
  };
  $('month-prev').onclick=()=>{month=new Date(month.getFullYear(),month.getMonth()-1,1);selected=iso(month);render();};
  $('month-next').onclick=()=>{month=new Date(month.getFullYear(),month.getMonth()+1,1);selected=iso(month);render();};
  $('calendar-today').onclick=()=>{selected=today;month=new Date(new Date().getFullYear(),new Date().getMonth(),1);render();};
  ['plans-subject','plans-status'].forEach(id=>$(id).onchange=render); $('plans-search').oninput=render;
  $('plans-reset').onclick=()=>{$('plans-subject').value='all';$('plans-status').value='all';$('plans-search').value='';render();};
  $('include-archived').onchange=load;
  const view=list=>{
    $('list-view').hidden=!list; $('calendar-view').hidden=list;
    $('view-list').setAttribute('aria-pressed',String(list));$('view-calendar').setAttribute('aria-pressed',String(!list));
  };
  $('view-calendar').onclick=()=>view(false);$('view-list').onclick=()=>view(true);
  $('plans-exams').onclick=async()=>{
    await window.openExamSchedule();
    $('study-schedule-dialog')?.addEventListener('close',()=>load(),{once:true});
  };
  await load();
});

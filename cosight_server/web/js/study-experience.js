/* Student-facing reading, quizzes and evidence-backed task details. */
(() => {
  const base='/api/nae-deep-research/v1/deep-research';
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const md=text=>{const n=el('div',undefined,'markdown-body');n.innerHTML=window.safeMarkdown(String(text||''));return n;};
  async function request(url,options={}) {const r=await fetch(url,options);const v=await r.json();if(!r.ok)throw Error(v.detail||v.error||'操作失败，请重试');return v;}
  const json=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function dialog(title){
    const d=el('dialog',undefined,'study-reader');const head=el('header');head.append(el('h2',title));
    const close=el('button','关闭');close.type='button';close.onclick=()=>d.close();head.append(close);d.append(head);
    const body=el('section',undefined,'study-reader-body');d.append(body);document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal();return {d,body,head};
  }
  function read(text){const {body,head}=dialog('放大阅读');body.append(md(text));const copy=el('button','复制全文');copy.onclick=async()=>{try{await navigator.clipboard.writeText(text);copy.textContent='已复制';}catch{copy.textContent='复制失败，请手动选择';}};head.prepend(copy);}
  let run=null;
  const colors={pass:'#67a68e',uncertain:'#c798b3',fail:'#b75f85',reviewing:'#aa9dbf'};
  function paint(){if(!run)return;document.querySelectorAll('.node').forEach(node=>{
    const index=Number(node.__data__?.id)-1,name=run.steps?.[index],circle=node.querySelector('.node-circle');if(!circle||!name)return;
    const review=run.credibility?.[name];circle.style.setProperty('fill',colors[review?.status]||'#bbb8c5','important');
    circle.style.stroke=run.step_statuses?.[name]==='in_progress'?'#6155a9':'#fff';circle.style.strokeWidth='3px';
    circle.style.strokeDasharray=run.step_statuses?.[name]==='in_progress'?'5 3':'none';
  });}
  function details(index){
    const {body}=dialog(index===undefined?'任务详情':'步骤详情');
    if(!run){body.append(el('p','暂无任务记录。'));return;}
    body.append(el('h3',run.title||'完整引擎任务'));
    body.append(el('p',`开始于 ${new Date(run.started_at*1000).toLocaleString('zh-CN')} · ${run.run_status==='completed'?'已完成':run.run_status==='running'?'执行中':'未完整完成'}`));
    if(run.notice)body.append(el('p',run.notice));
    body.append(el('p','颜色：绿＝复核未发现问题；粉＝待核对；深粉＝发现问题；灰＝未评估。模型复核不保证答案正确。'));
    (run.steps||[]).forEach((name,i)=>{if(index!==undefined&&i!==index)return;
      const card=el('article',undefined,'study-detail-card'),review=run.credibility?.[name];
      const status=run.step_statuses?.[name],labels={completed:'完成',in_progress:'执行中',blocked:'未完成',not_started:'未开始'};
      card.append(el('h3',`${i+1}. ${name}`),el('p',labels[status]||status));
      if(status==='in_progress')card.append(el('p',`已运行 ${Math.max(0,Math.round((Date.now()/1000-(run.step_started?.[name]||Date.now()/1000))))} 秒；超时将停止并提示重试。`));
      card.append(el('p',review?.basis||'尚未评估'));(review?.issues||[]).forEach(v=>card.append(el('p','需注意：'+v)));
      const output=el('details');output.append(el('summary','查看本步骤结果'),md(run.step_notes?.[name]||'暂无结果'));card.append(output);body.append(card);
    });
  }
  window.openStudyStep=id=>details(Number(id)-1);
  window.updateStudyRun=value=>{run=value;paint();let label=document.getElementById('study-run-details');if(!label){label=el('button','任务详情与可信度');label.id='study-run-details';label.type='button';label.onclick=()=>details();document.querySelector('.dag-preview-header')?.append(label);}if(label)label.textContent=value.run_status==='running'?'执行详情与可信度':'最近任务详情与可信度';};
  async function openQuiz(token){
    const {body,head}=dialog('随堂小测');body.textContent='正在读取题目…';
    try {
      await window.authReady;
      const data=await request(base+'/quizzes/'+token),questions=data.questions;
      const key='cosight:quiz:'+window.currentAccount.id+':'+token;
      let answers={};try{answers=JSON.parse(localStorage.getItem(key)||'{}');}catch{}
      let index=0;body.replaceChildren();const counter=el('p'),content=el('div'),nav=el('nav'),notice=el('p');
      const prev=el('button','上一题'),next=el('button','下一题'),submit=el('button','提交批改');nav.append(prev,next,submit);body.append(counter,content,nav,notice);
      const save=()=>{localStorage.setItem(key,JSON.stringify(answers));counter.textContent=`第 ${index+1} / ${questions.length} 题 · 已作答 ${questions.filter(q=>answers[q.id]).length} 题（自动保存）`;};
      function render(){const q=questions[index];content.replaceChildren(md(q.stem));content.prepend(el('p',q.q_type));
        if(['选择','多选','判断'].includes(q.q_type)){
          const opts=q.q_type==='判断'?['正确','错误']:q.options;
          opts.forEach((option,i)=>{const label=el('label',undefined,'study-quiz-option'),input=el('input');input.type=q.q_type==='多选'?'checkbox':'radio';input.name='answer';input.value=q.q_type==='判断'?option:String.fromCharCode(65+i);input.checked=q.q_type==='多选'?String(answers[q.id]||'').includes(input.value):answers[q.id]===input.value;
            input.onchange=()=>{answers[q.id]=q.q_type==='多选'?[...content.querySelectorAll('input:checked')].map(n=>n.value).join(''):input.value;save();};label.append(input,md(option));content.append(label);});
        }else{
          const text=el('textarea');text.rows=7;text.value=answers[q.id]||'';text.placeholder='输入答案或解答过程';text.oninput=()=>{answers[q.id]=text.value;save();};content.append(text);
          const photo=el('input');photo.type='file';photo.accept='image/*';photo.setAttribute('aria-label','上传或拍摄解答图片');photo.setAttribute('capture','environment');
          photo.onchange=async()=>{if(!photo.files[0])return;photo.disabled=true;notice.textContent='正在识别，请稍候…';try{const form=new FormData();form.append('files',photo.files[0]);const upload=await request('/api/nae-deep-research/v1/upload',{method:'POST',body:form});const read=await request(base+'/quizzes/'+token+'/read-answer',json({upload_id:upload.data[0].upload_id}));text.value=read.text;answers[q.id]=read.text;save();notice.textContent=read.notice;}catch(e){notice.textContent=e.message;}finally{photo.disabled=false;}};content.append(el('p','也可以上传或拍摄手写解答。识别后请核对文字再提交。'),photo);
        }prev.disabled=index===0;next.disabled=index===questions.length-1;save();}
      prev.onclick=()=>{index--;render();};next.onclick=()=>{index++;render();};
      submit.onclick=async()=>{submit.disabled=true;notice.textContent='正在批改…';try{
        const result=await request('/quiz/'+token+'/submit',json({answers}));content.replaceChildren(el('h3',`得分率 ${result.score}%`));counter.textContent='小测完成';nav.hidden=true;
        result.details.forEach((item,i)=>{const card=el('article',undefined,'study-detail-card');card.append(el('h3',`第${i+1}题 · ${item.correct?'正确':'需要复习'}`),md(questions[i].stem),md('你的答案：'+(item.user_answer||'未作答')),md('参考答案：'+item.answer),md('解析：'+item.explanation+(item.comment?'\n'+item.comment:'')));
          const add=el('button','加入错题本');add.onclick=async()=>{try{await request(base+'/quizzes/'+token+'/mistakes/'+questions[i].id,json({}));add.disabled=true;add.textContent='已保存';}catch(e){notice.textContent=e.message;}};card.append(add);content.append(card);});notice.textContent=result.next_step;localStorage.removeItem(key);
      }catch(e){notice.textContent=e.message;submit.disabled=false;}};render();
    }catch(e){body.textContent=e.message;}
  }
  document.addEventListener('click',e=>{
    if(e.target.closest('#credibility-overview-btn')){e.preventDefault();e.stopImmediatePropagation();details();return;}
    const node=e.target.closest('.node');if(node&&node.__data__&&run){e.preventDefault();e.stopImmediatePropagation();details(Number(node.__data__.id)-1);return;}
    const link=e.target.closest('a[href]');if(!link)return;const url=new URL(link.href,location.href);const m=url.pathname.match(/^\/quiz\/([a-f0-9]{16,80})$/);if(m&&url.origin===location.origin){e.preventDefault();openQuiz(m[1]);}
  },true);
  function decorate(){const bubbles=document.querySelectorAll('.chat-msg.ai .chat-bubble');const reader=document.getElementById('chat-read-answer');if(reader)reader.disabled=!bubbles.length;bubbles.forEach(bubble=>{if(bubble.querySelector('.study-answer-actions'))return;
    const actions=el('div',undefined,'study-answer-actions'),expand=el('button','放大阅读');expand.type='button';expand.onclick=()=>read(bubble.dataset.sourceText||bubble.innerText.replace('放大阅读',''));actions.append(expand);bubble.prepend(actions);
  });}
  document.addEventListener('DOMContentLoaded',async()=>{
    const header=document.querySelector('.chat-panel-header');if(header){
      const reader=el('button','放大阅读','study-header-reader');reader.id='chat-read-answer';reader.type='button';reader.title='全屏阅读最新一条回答';reader.disabled=true;
      reader.onclick=()=>{const bubbles=document.querySelectorAll('.chat-msg.ai .chat-bubble');const bubble=bubbles[bubbles.length-1];if(bubble)read(bubble.dataset.sourceText||bubble.innerText.replace('放大阅读',''));};
      header.insertBefore(reader,header.querySelector('.chat-collapse-btn'));
    }
    new MutationObserver(decorate).observe(document.getElementById('chat-messages')||document.body,{childList:true,subtree:true});decorate();
    try{if(!await window.authReady)return;const quizPath=location.pathname.match(/^\/quiz\/([a-f0-9]{16,80})$/);if(quizPath)openQuiz(quizPath[1]);const data=await request(base+'/runs');if(data.runs[0]){if(typeof createDag==='function')createDag({content:data.runs[0].snapshot});window.updateStudyRun(data.runs[0].snapshot);}}catch{}
    const panel=document.getElementById('personal-task-form');if(panel){
      const area=el('section',undefined,'study-parse-area'),text=el('textarea');text.placeholder='例如：明天19点到20点在图书馆讨论物理实验报告，比较重要';text.rows=3;
      const parse=el('button','识别并预填'),preview=el('div');parse.type='button';area.append(el('p','一句话添加事项'),text,parse,preview);panel.prepend(area);
      parse.onclick=async()=>{parse.disabled=true;preview.textContent='识别中…';try{const result=await request(base+'/parse-tasks',json({text:text.value}));preview.replaceChildren();
        result.tasks.forEach(task=>{const pick=el('button',`${task.date||'日期待确认'} · ${task.title} · 点击填入`);pick.type='button';pick.onclick=()=>{
          document.getElementById('personal-title').value=task.title;document.getElementById('personal-date').value=task.date||'';document.getElementById('personal-minutes').value=task.est_minutes;document.getElementById('personal-priority').value=task.priority;
          document.getElementById('personal-notes').value=[task.start_time&&task.end_time?task.start_time+'—'+task.end_time:'',task.notes].filter(Boolean).join('\n');
          document.getElementById('personal-error').textContent=(task.warnings||[]).join('；')||'已预填，请检查后保存；尚未创建事项。';};preview.append(pick);});
      }catch(e){preview.textContent=e.message;}finally{parse.disabled=false;}};
    }
    setInterval(paint,2000);
  });
})();

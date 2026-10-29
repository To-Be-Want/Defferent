document.addEventListener('DOMContentLoaded', async () => {
  if (!await window.authReady) return;
  document.getElementById('home-today').onclick = () => document.getElementById('open-today-plan').click();
  document.getElementById('home-exam').onclick = () => document.getElementById('open-study-schedule').click();
  document.getElementById('home-materials').onclick = () => document.getElementById('open-material-library').click();
  const setText = (id, text) => { document.getElementById(id).textContent = text; };
  const loadCard = async (path, errorId, render) => {
    try {
      const response = await fetch('/api/nae-deep-research/v1/deep-research' + path, {cache: 'no-store'});
      if (!response.ok) {
        const error = new Error('HTTP ' + response.status);
        error.status = response.status;
        throw error;
      }
      render(await response.json());
    } catch (error) {
      console.warn('首页模块读取失败：' + path, error);
      setText(errorId, error.status === 401 ? '登录已过期，请重新登录' : '暂时无法读取，请刷新重试');
    }
  };
  window.refreshStudyDashboard = async () => {
    await Promise.all([
      loadCard('/today-plan', 'home-task-time', plan => {
        // Support both historical and current task response shapes.
        const tasks = Array.isArray(plan.task_items) ? plan.task_items : (Array.isArray(plan.tasks) ? plan.tasks : null);
        if (!tasks) throw new Error('任务数据格式不匹配');
        const completed = Number.isFinite(plan.completed_count) ? plan.completed_count : tasks.filter(task => typeof task === 'object' && ['完成', '已完成'].includes(task.status)).length;
        setText('home-task-count', `今日 ${tasks.length} 项任务`);
        setText('home-task-time', `已完成 ${completed} 项 · 预计 ${Number(plan.total_minutes) || 0} 分钟`);
      }),
      loadCard('/materials', 'home-material-count', materials => {
        if (!Array.isArray(materials.materials)) throw new Error('资料数据格式不匹配');
        setText('home-material-count', `${materials.materials.length} 份资料 · 点击管理`);
      }),
      loadCard('/study-schedule', 'home-next-exam', schedule => {
        if (!Array.isArray(schedule.subjects)) throw new Error('考试数据格式不匹配');
        const now = new Date(); now.setHours(0, 0, 0, 0);
        const upcoming = schedule.subjects.map(subject => ({...subject, date: new Date(subject.exam_date + 'T00:00:00')}))
          .filter(subject => subject.exam_status !== 'completed' && subject.date >= now).sort((a, b) => a.date - b.date)[0];
        const overdue = schedule.subjects.filter(subject => subject.display_status === 'overdue').length;
        setText('home-next-exam', (upcoming ? `${upcoming.name} · ${upcoming.exam_date}` : '暂无待考试 · 点击添加') + (overdue ? ` · ${overdue} 门待确认` : ''));
      })
    ]);
  };
  await window.refreshStudyDashboard();
});

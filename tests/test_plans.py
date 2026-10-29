"""Plan overview and personal tasks use disposable accounts and no paid models."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_multi_user as existing
STUDY = existing.STUDY
import study_db
import scheduler


class PlanTests(unittest.TestCase):
    setUp = existing.MultiUserTests.setUp
    tearDown = existing.MultiUserTests.tearDown
    register = existing.MultiUserTests.register
    own = existing.MultiUserTests.own

    def test_personal_tasks_crud_today_chat_and_isolation(self):
        today=date.today().isoformat()
        body={"title":"提交实验报告 <script>alert(1)</script>","date":today,"priority":"high","est_minutes":45,"notes":"小组汇报"}
        with patch('requests.post') as model:
            response=self.a.post(STUDY+'/manual-tasks',json=body)
            self.assertEqual(response.status_code,201,response.text)
            task=response.json()
            self.assertEqual(self.guest.get(STUDY+'/plan-overview').status_code,401)
            self.assertEqual(self.b.get(STUDY+'/plan-overview').json()['tasks'],[])
            self.assertEqual(self.b.patch(STUDY+f"/manual-tasks/{task['id']}",json={"status":"完成"}).status_code,404)
            self.assertEqual(self.b.delete(STUDY+f"/manual-tasks/{task['id']}").status_code,404)
            daily=self.a.get(STUDY+'/today-plan').json()
            self.assertEqual(daily['total_minutes'],45)
            self.assertEqual(daily['task_items'][0]['source'],'manual')
            from app.cosight.tool.study_toolkit import StudyToolkit
            self.assertIn('实验报告',self.own(self.ua,lambda:StudyToolkit().get_today_tasks()))
            completed=self.a.patch(STUDY+f"/manual-tasks/{task['id']}",json={"status":"完成"})
            self.assertEqual(completed.status_code,200)
            self.assertEqual(self.a.get(STUDY+'/today-plan').json()['completed_count'],1)
            moved=(date.today()+timedelta(days=2)).isoformat()
            self.a.patch(STUDY+f"/manual-tasks/{task['id']}",json={"date":moved,"title":"报告改期"})
            self.assertFalse(self.a.get(STUDY+'/today-plan').json()['found'])
            self.assertTrue(self.a.get(STUDY+'/today-plan',params={'date':moved}).json()['found'])
            # A fresh connection reads persisted items, not process memory.
            self.assertEqual(self.own(self.ua,lambda:__import__('cosight_server.deep_research.services.study_plans',fromlist=['overview']).overview())['tasks'][0]['title'],'报告改期')
            self.assertEqual(self.a.delete(STUDY+f"/manual-tasks/{task['id']}").status_code,200)
            self.assertEqual(self.a.get(STUDY+'/plan-overview').json()['summary']['total'],0)
            model.assert_not_called()

    def test_browser_calendar_list_personal_tasks_and_home_sync(self):
        import os, socket, threading, time
        import uvicorn
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        def seed():
            sid=study_db.add_subject('通信原理',(date.today()+timedelta(days=20)).isoformat())
            with study_db.get_conn() as conn:
                for day in (date.today(),date.today()+timedelta(days=3)):
                    conn.execute("INSERT INTO plans(version,date,subject_id,task_type,est_minutes) VALUES (1,?,?,'真题练习',30)",(day.isoformat(),sid))
        self.own(self.ua,seed)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True)
        with patch.dict(os.environ,{'COSIGHT_COOKIE_SECURE':'false'}):
            thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge',headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    origin=f'http://127.0.0.1:{port}'
                    page.goto(origin+'/cosight/login.html')
                    page.fill('#username','alice');page.fill('#password','test-password-1234');page.click('#submit')
                    page.wait_for_url('**/cosight/')
                    page.click('.sidebar-navigation a[href="/cosight/plans.html"]')
                    page.wait_for_selector('#day-tasks .plan-task')
                    self.assertEqual(page.locator('#day-tasks .plan-task').count(),1)
                    page.click('#add-personal-task')
                    title='提交实验报告 <script>window.planAttack=1</script>'
                    page.fill('#personal-title',title)
                    page.select_option('#personal-priority','high')
                    page.click('#personal-save')
                    page.wait_for_selector('#personal-task-dialog',state='hidden')
                    page.wait_for_function("document.querySelectorAll('#day-tasks .plan-task').length===2")
                    self.assertIsNone(page.evaluate('window.planAttack'))
                    page.reload();page.wait_for_function("document.querySelectorAll('#day-tasks .plan-task').length===2")
                    page.click('#view-list')
                    self.assertFalse(page.locator('#calendar-view').is_visible())
                    self.assertEqual(page.locator('#all-plan-tasks .plan-task').count(),3)
                    page.click('#view-calendar')
                    future=(date.today()+timedelta(days=3)).isoformat()
                    # Select the correct calendar month even when the fixture crosses a month boundary.
                    while page.locator(f'#calendar-days button[data-date="{future}"]').count()==0: page.click('#month-next')
                    page.click(f'#calendar-days button[data-date="{future}"]')
                    self.assertEqual(page.locator('#day-tasks .plan-task').count(),1)
                    page.click('#calendar-today')
                    page.select_option('#plans-subject','manual')
                    page.locator('#day-tasks input[type=checkbox]').check()
                    page.wait_for_function("document.querySelector('#day-tasks .plan-task')?.classList.contains('is-complete')")
                    page.goto(origin+'/cosight/')
                    page.click('#open-today-plan')
                    page.wait_for_selector('.task-complete-button[data-task-source=manual]')
                    self.assertEqual(page.locator('.task-complete-button[data-task-source=manual]').inner_text(),'撤销完成')
                    page.locator('.task-complete-button[data-task-source=manual]').click()
                    page.wait_for_function("document.querySelector('.task-complete-button[data-task-source=manual]')?.textContent==='标记完成'")
                    page.goto(origin+'/cosight/plans.html')
                    page.wait_for_selector('#day-tasks .plan-task-actions')
                    page.locator('#day-tasks .plan-task-actions button').filter(has_text='编辑').click()
                    tomorrow=(date.today()+timedelta(days=1)).isoformat()
                    page.fill('#personal-date',tomorrow);page.fill('#personal-title','实验报告改期')
                    page.click('#personal-save');page.wait_for_selector('#personal-task-dialog',state='hidden')
                    page.wait_for_function("document.getElementById('day-tasks').textContent.includes('实验报告改期')")
                    screenshots=Path(__file__).resolve().parents[1]/'artifacts';screenshots.mkdir(exist_ok=True)
                    page.screenshot(path=str(screenshots/'plans-overview-desktop.png'),full_page=True)
                    page.click('#view-list');page.screenshot(path=str(screenshots/'plans-overview-list.png'),full_page=True);page.click('#view-calendar')
                    for width in (390,320):
                        page.set_viewport_size({'width':width,'height':844})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+2)
                    page.screenshot(path=str(screenshots/'plans-overview-mobile.png'),full_page=True)
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.locator('#day-tasks .plan-task-actions button').filter(has_text='删除').click()
                    page.wait_for_function("document.querySelectorAll('#day-tasks .plan-task').length===0")
                    self.assertFalse(errors,errors)
                    browser.close()
            finally:
                server.should_exit=True;thread.join(timeout=10)

    def test_validation_and_csrf(self):
        for body in ({'title':'','date':date.today().isoformat()}, {'title':'x','date':'2026-02-30'},
                     {'title':'x','date':date.today().isoformat(),'est_minutes':-1},
                     {'title':'x','date':date.today().isoformat(),'est_minutes':True},
                     {'title':'x','date':date.today().isoformat(),'priority':'unknown'},
                     {'title':'x','date':date.today().isoformat(),'user_id':self.ub['id']}):
            self.assertEqual(self.a.post(STUDY+'/manual-tasks',json=body).status_code,400,body)
        self.assertEqual(self.a.post(STUDY+'/manual-tasks',headers={'X-CSRF-Token':'wrong'},json={'title':'x','date':date.today().isoformat()}).status_code,403)

    def test_overview_uses_each_subject_latest_and_preserves_manual_on_replan(self):
        future=(date.today()+timedelta(days=20)).isoformat()
        def seed():
            a=study_db.add_subject('高数',future); b=study_db.add_subject('英语',future)
            study_db.add_block(a,'微积分','极限'); study_db.add_block(b,'阅读','阅读理解')
            with study_db.get_conn() as conn:
                for version,sid,name in ((1,a,'旧版本'),(2,a,'高数新版本'),(1,b,'英语当前版本')):
                    conn.execute('INSERT INTO plans(version,date,subject_id,task_type,est_minutes) VALUES (?,?,?,?,30)',(version,date.today().isoformat(),sid,name))
            return a,b
        a,b=self.own(self.ua,seed)
        personal=self.a.post(STUDY+'/manual-tasks',json={'title':'组会','date':date.today().isoformat()}).json()
        items=self.a.get(STUDY+'/plan-overview').json()['tasks']
        self.assertEqual(len(items),3)
        self.assertNotIn('旧版本',[item['task_type'] for item in items])
        self.assertEqual(len({item['key'] for item in items}),3)
        self.own(self.ua,lambda:scheduler.build_plan(verbose=False))
        self.assertTrue(any(item['source']=='manual' and item['id']==personal['id'] for item in self.a.get(STUDY+'/plan-overview').json()['tasks']))
        def finish():
            with study_db.get_conn() as conn: conn.execute("UPDATE subjects SET exam_status='completed' WHERE id=?",(a,))
        self.own(self.ua,finish)
        visible=self.a.get(STUDY+'/plan-overview').json()['tasks']
        self.assertFalse(any(item['subject_id']==a for item in visible))
        history=self.a.get(STUDY+'/plan-overview?include_archived=true').json()['tasks']
        self.assertTrue(any(item['subject_id']==a for item in history))


if __name__=='__main__': unittest.main(verbosity=2)

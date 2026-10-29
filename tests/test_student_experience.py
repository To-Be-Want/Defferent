"""Regression coverage for the reported student workflows. Never calls a real model."""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_multi_user as existing
from cosight_server.deep_research.services import study_experience as exp
import study_db

QUIZ={'questions':[{'type':'选择','stem':f'第{i}题：求导数','options':['A. 2x','B. x'],
    'answer':'A','explanation':'幂函数求导。'} for i in range(1,4)]}


class StudentExperienceTests(unittest.TestCase):
    setUp=existing.MultiUserTests.setUp
    tearDown=existing.MultiUserTests.tearDown
    register=existing.MultiUserTests.register
    own=existing.MultiUserTests.own

    def enable(self):self.a.patch(existing.STUDY+'/settings',json={'allow_cloud_model':True})

    def test_quiz_without_blocks_and_private_answers(self):
        self.enable()
        with patch.object(exp,'model_json',return_value=QUIZ):
            reply=self.own(self.ua,lambda:exp.create_quiz('出一个小测，共3题'))
        token=reply.split('/quiz/')[1].split(')')[0]
        result=self.a.get(existing.STUDY+'/quizzes/'+token)
        self.assertEqual(result.status_code,200);questions=result.json()['questions']
        self.assertEqual(len(questions),3);self.assertNotIn('answer',questions[0])
        self.assertEqual(self.b.get(existing.STUDY+'/quizzes/'+token).status_code,404)
        answers={str(q['id']):'A' for q in questions}
        grade=self.a.post('/quiz/'+token+'/submit',json={'answers':answers})
        self.assertEqual(grade.status_code,200,grade.text);self.assertEqual(grade.json()['score'],100)
        saved=self.a.post(existing.STUDY+'/quizzes/'+token+'/mistakes/'+str(questions[0]['id']),json={})
        self.assertEqual(saved.status_code,200,saved.text)

    def test_truncated_quiz_not_saved(self):
        self.enable()
        with patch.object(exp,'model_json',return_value={'questions':QUIZ['questions'][:2]}):
            with self.assertRaisesRegex(ValueError,'不完整'):self.own(self.ua,lambda:exp.create_quiz('共3题'))
        def count():
            with study_db.get_conn() as conn:return conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0]
        self.assertEqual(self.own(self.ua,count),0)

    def test_confirmation_resumes_quiz_before_engine_and_save(self):
        self.enable()
        from cosight_server.deep_research.services import study_gateway as gateway
        from user_context import scoped_session_id
        self.own(self.ua,lambda:gateway.save_message(scoped_session_id('flow'),'assistant','没问题我就出个小测。'))
        self.own(self.ua,lambda:exp.remember_action(scoped_session_id('flow'),'没问题我就出个小测。'))
        with patch.object(exp,'model_json',return_value=QUIZ),patch('study_fast_channel._should_fast',side_effect=AssertionError('no router')):
            result=self.a.post(existing.STUDY+'/search',json={'content':[{'value':'没问题'}],'sessionInfo':{'sessionId':'flow'}})
        self.assertEqual(result.status_code,200,result.text);self.assertIn('开始答题',result.text)

    def test_existing_save_confirmation_is_not_hijacked(self):
        import study_fast_channel as fast
        fast._pending_selection['existing-save']={'questions':[]}
        try:
            result=self.own(self.ua,lambda:exp.intercept('existing-save','可以',[{'role':'assistant','content':'保存这道错题吗？'}]))
            self.assertIsNone(result)
        finally:fast._pending_selection.pop('existing-save',None)

    def test_subject_content_classification_and_manual_priority(self):
        from cosight_server.deep_research.services import study_library as lib
        with patch('material_analyst.extract_pdf_text',return_value='高等数学测试资料 f(x)=x²，导数'):
            upload=self.a.post(existing.PREFIX+'/upload',files={'files':('screenshot.png',b'fake-image','image/png')}).json()['data'][0]
        self.assertEqual(upload['subject'],'高等数学')
        self.a.patch(existing.STUDY+'/materials/'+upload['upload_id'],json={'subject':'数学分析'})
        def classify_again():return lib.learn_subject_from_text(lib.material_path(upload['upload_id']),'高等数学')
        self.assertFalse(self.own(self.ua,classify_again))
        self.assertEqual(self.a.get(existing.STUDY+'/materials').json()['materials'][0]['subject'],'数学分析')

    def test_different_subject_plan_preflight_does_not_run_deep(self):
        self.enable()
        with patch('study_fast_channel._should_fast',side_effect=AssertionError('no router')):
            result=self.a.post(existing.STUDY+'/search',json={'content':[{'value':'我要准备大学物理考试，请根据已有资料制定复习计划。'}]})
        self.assertIn('没有找到大学物理资料',result.text);self.assertNotIn('最终学习报告',result.text)

    def test_task_parse_previews_only_and_no_cloud_fallback(self):
        result=self.a.post(existing.STUDY+'/parse-tasks',json={'text':'明天去图书馆讨论'})
        self.assertEqual(result.status_code,200);self.assertFalse(result.json()['saved'])
        important=self.a.post(existing.STUDY+'/parse-tasks',json={'text':'明天到图书馆讨论事务，较重要'}).json()['tasks'][0]
        self.assertEqual(important['priority'],'high')
        self.assertEqual(self.a.get(existing.STUDY+'/plan-overview').json()['tasks'],[])
        self.enable()
        with patch.object(exp,'model_json',return_value={'tasks':[{'title':'开会','date':'2026-11-02','est_minutes':60,'priority':'high','notes':'19:00—20:00'}]}):
            self.assertEqual(self.a.post(existing.STUDY+'/parse-tasks',json={'text':'下周一开会'}).status_code,200)

    def test_task_parse_nullable_duration_and_warnings(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        self.enable()
        for duration in (None, '', '未说明', {}):
            with self.subTest(duration=duration), patch.object(exp,'model_json',return_value={'tasks':[{'title':'到图书馆讨论事务','date':None,'est_minutes':duration,'priority':'normal','notes':None,'warnings':None}]}):
                response=self.a.post(existing.STUDY+'/parse-tasks',json={'text':'明天到图书馆讨论事务，较重要'})
                self.assertEqual(response.status_code,200,response.text)
                task=response.json()['tasks'][0]
                self.assertEqual(task['est_minutes'],30)
                self.assertEqual(task['priority'],'high')
                self.assertEqual(task['date'],(datetime.now(ZoneInfo('Asia/Shanghai')).date()+timedelta(days=1)).isoformat())
                self.assertTrue(any('30分钟' in warning for warning in task['warnings']))
        self.assertEqual(self.a.get(existing.STUDY+'/plan-overview').json()['tasks'],[])

    def test_reasoning_never_used_as_answer(self):
        from types import SimpleNamespace
        import material_analyst as ma
        response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':'','reasoning_content':'private reasoning'},'finish_reason':'stop'}]})
        with patch('requests.post',return_value=response):
            with self.assertRaises(ValueError):ma.chat([{'role':'user','content':'test'}],{'base_url':'https://example.test','api_key':'test','model':'test'})

    def test_photo_answer_is_scoped_and_requires_confirmation(self):
        self.enable()
        with patch.object(exp,'model_json',return_value=QUIZ):reply=self.own(self.ua,lambda:exp.create_quiz('共3题'))
        token=reply.split('/quiz/')[1].split(')')[0]
        with patch('material_analyst.extract_pdf_text',return_value='解答：2x'):
            upload=self.a.post(existing.PREFIX+'/upload',files={'files':('answer.png',b'fake-image','image/png')}).json()['data'][0]
            result=self.a.post(existing.STUDY+'/quizzes/'+token+'/read-answer',json={'upload_id':upload['upload_id']})
        self.assertEqual(result.status_code,200);self.assertTrue(result.json()['requires_confirmation'])
        self.assertEqual(self.b.post(existing.STUDY+'/quizzes/'+token+'/read-answer',json={'upload_id':upload['upload_id']}).status_code,404)

    def test_run_credibility_persistence_and_isolation(self):
        self.enable()
        from cosight_server.deep_research.services.study_deep_engine import run_deep
        def model(messages,*args,**kwargs):
            system=messages[0]['content']
            if '规划器' in system:return json.dumps({'title':'证明核对','steps':[{'name':'证明','instruction':'推导','depends_on':[]},{'name':'检查','instruction':'核对','depends_on':[0]}]})
            if '复核员' in system:return json.dumps({'status':'pass','basis':'代回原式逐项一致','issues':[]})
            return '推理与结论。'
        with patch('material_analyst.sniff_llm_config',return_value={'base_url':'test','api_key':'test','model':'test'}),patch('material_analyst.chat',side_effect=model),patch('material_analyst.load_env',return_value={}):
            self.own(self.ua,lambda:run_deep('证明并核对',[]))
        saved=self.a.get(existing.STUDY+'/runs').json()['runs'][0]['snapshot']
        self.assertEqual(saved['run_status'],'completed');self.assertEqual(saved['credibility']['证明']['status'],'pass')
        self.assertEqual(self.b.get(existing.STUDY+'/runs').json()['runs'],[])

    def test_browser_quiz_reader_and_task_prefill(self):
        import uvicorn
        from playwright.sync_api import sync_playwright
        self.enable()
        with patch.object(exp,'model_json',return_value=QUIZ):reply=self.own(self.ua,lambda:exp.create_quiz('共3题'))
        token=reply.split('/quiz/')[1].split(')')[0]
        with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=port,log_level='error'))
        with patch.dict(os.environ,{'COSIGHT_COOKIE_SECURE':'false'}):
            worker=threading.Thread(target=server.run,daemon=True);worker.start()
            try:
                for _ in range(100):
                    if server.started:break
                    time.sleep(.05)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{port}/cosight/login.html');page.fill('#username','alice');page.fill('#password','test-password-1234');page.click('#submit');page.wait_for_url('**/cosight/')
                    page.wait_for_function("window.currentAccount && document.getElementById('home-task-count').textContent.includes('0')")
                    page.evaluate("text=>{appendChatMessage(text,'ai');document.querySelector('.initial-input-container').classList.add('hidden');document.querySelector('.middle-container').classList.add('show');expandChatPanel();}",reply)
                    page.get_by_role('link',name='开始答题').click();page.wait_for_selector('dialog[open]')
                    self.assertNotIn('参考答案',page.locator('dialog').inner_text());page.locator('dialog input[type=radio]').first.check()
                    page.get_by_role('button',name='下一题',exact=True).click();page.get_by_role('button',name='关闭',exact=True).click()
                    page.get_by_role('link',name='开始答题').click();page.locator('dialog input:checked').wait_for();self.assertEqual(page.locator('dialog input:checked').count(),1)
                    page.get_by_role('button',name='提交批改').click();page.get_by_text('小测完成',exact=True).wait_for();page.get_by_role('button',name='关闭',exact=True).click()
                    page.evaluate("appendChatMessage('完整证明\\n\\n'+('详细证明步骤。\\n\\n'.repeat(70)),'ai');document.getElementById('chat-messages').scrollTop=999999;")
                    page.locator('#chat-read-answer').wait_for(state='visible');self.assertTrue(page.locator('#chat-read-answer').is_enabled())
                    page.locator('#chat-read-answer').click();self.assertTrue(page.locator('dialog').is_visible())
                    self.assertIn('完整证明',page.locator('dialog').inner_text())
                    self.assertGreater(page.locator('dialog').bounding_box()['width'],1200)
                    page.keyboard.press('Escape')
                    page.goto(f'http://127.0.0.1:{port}/cosight/plans.html');page.click('#add-personal-task');page.locator('.study-parse-area textarea').fill('明天开会')
                    with patch.object(exp,'model_json',return_value={'tasks':[{'title':'测试开会','date':'2026-11-02','est_minutes':None,'priority':'high','notes':'图书馆','warnings':None}]}):
                        page.get_by_role('button',name='识别并预填',exact=True).click();page.get_by_role('button',name='2026-11-02 · 测试开会 · 点击填入',exact=True).click()
                    self.assertEqual(page.input_value('#personal-title'),'测试开会')
                    self.assertEqual(page.input_value('#personal-minutes'),'30')
                    artifacts=Path('artifacts');artifacts.mkdir(exist_ok=True);page.screenshot(path=str(artifacts/'task-recognition.png'))
                    self.assertFalse(errors,errors);browser.close()
            finally:server.should_exit=True;worker.join(10)


if __name__=='__main__':unittest.main(verbosity=2)

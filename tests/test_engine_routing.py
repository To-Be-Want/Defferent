"""Exercises the real CoSight scheduler with fake models, never paid API requests."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_multi_user as existing
from user_context import current_user, user_root
from cosight_server.deep_research.services.study_deep_engine import run_deep, validate_plan
from app.cosight.task.task_manager import TaskManager

CONFIG={"api_key":"unit-test-only","base_url":"https://example.invalid/v1","model":"test"}
GRAPH={"title":"费马小定理证明","steps":[
    {"name":"剩余类置换证明","instruction":"独立完成剩余类证明","depends_on":[]},
    {"name":"群论证明","instruction":"独立完成群论证明","depends_on":[]},
    {"name":"核对条件与结论","instruction":"检查两份证明的条件","depends_on":[0,1]}]}


def fake_model(messages, config, **kwargs):
    who=current_user.get()["username"]
    system=messages[0]["content"]
    if "规划器" in system: return json.dumps(GRAPH,ensure_ascii=False)
    if "最终学习报告" in system or '面向大学生的学习助教' in system: return f"{who}：完整证明答案，已列出前提与推导。"
    return f"{who}：本步骤的推导结果。"


class EngineTests(unittest.TestCase):
    setUp=existing.MultiUserTests.setUp
    tearDown=existing.MultiUserTests.tearDown
    register=existing.MultiUserTests.register
    own=existing.MultiUserTests.own

    def allow(self,client):
        response=client.patch(existing.STUDY+'/settings',json={"allow_cloud_model":True,"allow_web_search":False})
        self.assertEqual(response.status_code,200)

    def test_every_idle_request_uses_semantic_engine_router(self):
        import study_fast_channel as fast
        with patch.object(fast.ma,'sniff_llm_config',return_value=CONFIG), patch.object(
                fast, '_classify_engine_by_llm', side_effect=['deep', 'fast']) as router:
            self.assertFalse(self.own(self.ua,lambda:fast._should_fast('请处理这个','fresh-session')))
            self.assertTrue(self.own(self.ua,lambda:fast._should_fast('也说说另一个','fresh-session')))
            self.assertEqual(router.call_count, 2)

    def test_engine_router_failure_and_low_confidence_prefer_deep(self):
        import study_fast_channel as fast
        response = SimpleNamespace()
        response.raise_for_status = lambda: None
        response.json = lambda: {"choices":[{"message":{"content":json.dumps(
            {"engine":"fast","confidence":.2,"reason":"uncertain"})}}]}
        with patch('study_fast_channel.requests.post',return_value=response):
            self.assertEqual(fast._classify_engine_by_llm('任意表达',CONFIG),'deep')
        with patch('study_fast_channel.requests.post',side_effect=TimeoutError('timeout')):
            self.assertEqual(fast._classify_engine_by_llm('任意表达',CONFIG),'deep')

    def test_original_scheduler_parallel_steps_context_and_progress(self):
        self.allow(self.a)
        events=[];seen=[]
        def model(*args,**kwargs):
            seen.append(current_user.get()['id'])
            return fake_model(*args,**kwargs)
        with patch('material_analyst.sniff_llm_config',return_value=CONFIG),patch('material_analyst.load_env',return_value={}),patch('material_analyst.chat',side_effect=model):
            reply=self.own(self.ua,lambda:run_deep('完整证明费马小定理',[],emit=events.append))
        self.assertIn('alice',reply)
        self.assertEqual(set(seen),{self.ua['id']})
        self.assertTrue(any('in_progress' in event['content']['step_statuses'].values() for event in events))
        self.assertEqual(set(events[-1]['content']['step_statuses'].values()),{'completed'})
        self.assertEqual(events[-1]['content']['dependencies']['2'],[0,1])
        self.assertFalse(TaskManager.plans)
        files=self.own(self.ua,lambda:list((user_root()/'work_space').glob('deep-*/result.json')))
        self.assertEqual(len(files),1)

    def test_two_accounts_cannot_mix_same_question_or_events(self):
        self.allow(self.a);self.allow(self.b)
        def worker(user):
            events=[]
            result=self.own(user,lambda:run_deep('证明费马定理',[],emit=events.append))
            return result,events[-1]['content']
        with patch('material_analyst.sniff_llm_config',return_value=CONFIG),patch('material_analyst.load_env',return_value={}),patch('material_analyst.chat',side_effect=fake_model):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                a,b=list(pool.map(worker,[self.ua,self.ub]))
        self.assertIn('alice',a[0]);self.assertNotIn('bob',json.dumps(a))
        self.assertIn('bob',b[0]);self.assertNotIn('alice',json.dumps(b))
        self.assertNotEqual(a[1]['plan_id'],b[1]['plan_id'])

    def test_bad_dag_rejected_and_blocked_dependency_does_not_execute(self):
        self.allow(self.a)
        cyclic={"steps":[{"name":"a","instruction":"a","depends_on":[1]},{"name":"b","instruction":"b","depends_on":[0]}]}
        with self.assertRaisesRegex(ValueError,'循环'): validate_plan(cyclic)
        with patch('material_analyst.sniff_llm_config',return_value=CONFIG),patch('material_analyst.load_env',return_value={}),patch('material_analyst.chat',return_value=json.dumps(cyclic)) as model:
            with self.assertRaisesRegex(RuntimeError,'任务图'):
                self.own(self.ua,lambda:run_deep('证明',[]))
            self.assertEqual(model.call_count,3)
        events=[];executed=[]
        def failing(messages,config,**kwargs):
            if '执行单个' in messages[0]['content']:
                step=json.loads(messages[1]['content'])['step'];executed.append(step)
                if '剩余类' in step: raise RuntimeError('simulated model failure')
            return fake_model(messages,config,**kwargs)
        with patch('material_analyst.sniff_llm_config',return_value=CONFIG),patch('material_analyst.load_env',return_value={}),patch('material_analyst.chat',side_effect=failing):
            reply=self.own(self.ua,lambda:run_deep('证明',[],emit=events.append))
        self.assertIn('未能完成',reply)
        self.assertNotIn('检查两份证明的条件',executed)
        self.assertIn('blocked',events[-1]['content']['step_statuses'].values())
        self.assertFalse(TaskManager.plans)

    def test_privacy_off_prevents_classifier_and_engine(self):
        with patch('study_fast_channel._classify_engine_by_llm',side_effect=AssertionError('no classifier')),patch('material_analyst.chat',side_effect=AssertionError('no model')):
            response=self.a.post(existing.STUDY+'/search',json={'content':[{'type':'text','value':'证明费马定理'}]})
        self.assertEqual(response.status_code,200)
        self.assertIn('未开启在线答疑',response.text)

    def test_websocket_routes_and_actual_dag_events(self):
        self.allow(self.a)
        path='wss://testserver/api/openans-support-chatbot/v1/robot/wss/messages'
        route=lambda query,*args,**kwargs: 'deep' if '费马' in query else 'fast'
        with patch('material_analyst.sniff_llm_config',return_value=CONFIG),patch('material_analyst.load_env',return_value={}),patch('material_analyst.chat',side_effect=fake_model),patch('study_fast_channel._classify_engine_by_llm',side_effect=route):
            with self.a.websocket_connect(path,headers={'Origin':'https://testserver'}) as socket:
                for query,engine in (('证明费马定理','deep'),('求函数x^2的导数','fast')):
                    with patch('study_fast_channel.try_fast_channel',return_value='快速解答：2x'):
                        socket.send_json({'action':'message','topic':'routing-test','data':json.dumps({'initData':[{'type':'text','value':query}],'sessionInfo':{'learningSessionId':'routing-test'}})})
                        messages=[]
                        while True:
                            message=socket.receive_json()['data'];messages.append(message)
                            if message['type']=='control-status-message':break
                    route=[item for item in messages if item['type']=='study-engine-route'][0]
                    self.assertEqual(route['engine'],engine)
                    graphs=[item for item in messages if item['type']=='lui-message-manus-step']
                    self.assertEqual(bool(graphs),engine=='deep')
                    if graphs:self.assertEqual(set(graphs[-1]['content']['step_statuses'].values()),{'completed'})

    def test_browser_shows_deep_graph_and_fast_label(self):
        import socket,threading,time
        import uvicorn
        from playwright.sync_api import sync_playwright
        self.allow(self.a)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True)
        with patch.dict(os.environ,{'COSIGHT_COOKIE_SECURE':'false'}),patch('material_analyst.sniff_llm_config',return_value=CONFIG),patch('material_analyst.load_env',return_value={}),patch('material_analyst.chat',side_effect=fake_model),patch('study_fast_channel._classify_engine_by_llm',side_effect=lambda text,*a,**k:'deep' if '证明' in text else 'fast'),patch('study_fast_channel.try_fast_channel',return_value='快速解答：导数为2x'):
            thread.start()
            try:
                for _ in range(100):
                    if server.started:break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge',headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{port}/cosight/login.html')
                    page.fill('#username','alice');page.fill('#password','test-password-1234');page.click('#submit')
                    page.wait_for_url('**/cosight/')
                    page.fill('#initial-message-input','证明费马定理');page.click('#initial-send-button')
                    page.wait_for_function("document.getElementById('study-engine-label')?.dataset.engine==='deep'")
                    page.wait_for_function("document.querySelectorAll('#dag-svg .node').length===3")
                    page.wait_for_function("document.getElementById('chat-messages').textContent.includes('完整证明答案')")
                    page.wait_for_function("""() => {
                      const area=document.getElementById('dag-svg').getBoundingClientRect();
                      return [...document.querySelectorAll('#dag-svg .node')].every(node=>{
                        const box=node.getBoundingClientRect();
                        return box.left>=area.left && box.right<=area.right && box.top>=area.top && box.bottom<=area.bottom;
                      });
                    }""")
                    page.evaluate("if(typeof hideStepsTooltip==='function') hideStepsTooltip()")
                    folder=Path(__file__).resolve().parents[1]/'artifacts';folder.mkdir(exist_ok=True)
                    page.screenshot(path=str(folder/'restored-deep-engine.png'),full_page=True)
                    page.locator('#dag-svg .node-circle').first.click()
                    page.get_by_role('heading',name='步骤详情',exact=True).wait_for()
                    self.assertIn('模型复核',page.locator('dialog[open]').inner_text())
                    page.keyboard.press('Escape')
                    page.click('#credibility-overview-btn')
                    page.get_by_role('heading',name='任务详情',exact=True).wait_for()
                    page.screenshot(path=str(folder/'credibility-details.png'),full_page=True)
                    page.keyboard.press('Escape')
                    page.fill('#message-input','求函数x^2的导数');page.click('#send-button')
                    page.wait_for_function("document.getElementById('study-engine-label')?.dataset.engine==='fast'")
                    page.wait_for_function("document.getElementById('chat-messages').textContent.includes('导数为2x')")
                    self.assertFalse(errors,errors)
                    browser.close()
            finally:
                server.should_exit=True;thread.join(timeout=10)

    @unittest.skipUnless(os.getenv('RUN_LIVE_ENGINE')=='1','live model check is opt-in')
    def test_live_model_deep_pipeline(self):
        self.allow(self.a)
        events=[]
        reply=self.own(self.ua,lambda:run_deep('请用反证法证明根号2是无理数，并独立核对互素与奇偶性的论证。请简洁完成。',[],emit=events.append))
        self.assertIn('无理',reply)
        self.assertEqual(set(events[-1]['content']['step_statuses'].values()),{'completed'})
        print('LIVE_DEEP_OK',len(events[-1]['content']['steps']),'steps',len(reply),'answer characters')


if __name__=='__main__':unittest.main(verbosity=2)

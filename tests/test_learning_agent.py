"""Real LangGraph/SQLite, fake model transport; no paid calls or original data."""
import json
import sys
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_multi_user as existing
import study_db
from app.cosight.tool.study_toolkit import StudyToolkit
from learning_agent import proposals, tools
from learning_agent.runtime import run_agent
from user_context import user_root


def call(name, **args):
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'test-call', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}


class AgentTests(unittest.TestCase):
    setUp = existing.MultiUserTests.setUp
    tearDown = existing.MultiUserTests.tearDown
    register = existing.MultiUserTests.register
    own = existing.MultiUserTests.own

    def seed(self):
        self.a.patch(existing.STUDY+'/settings', json={'allow_cloud_model': True})
        def data():
            sid = study_db.add_subject('程序设计', (date.today()+timedelta(days=12)).isoformat())
            bid = study_db.add_block(sid, '循环', '线性扫描', est_minutes=50)
            return sid, bid
        return self.own(self.ua, data)

    def run_as(self, query, sid='learning', user=None, **kwargs):
        return self.own(user or self.ua, lambda: run_agent(query, sid, [], [], **kwargs))

    def test_model_tools_loop_uses_scoped_learning_data(self):
        self.seed()
        with patch('learning_agent.model.complete', side_effect=[call('list_knowledge_blocks'),
                  {'role': 'assistant', 'content': '先按课件学习线性扫描。'}]) as complete:
            reply = self.run_as('今天学什么')
        self.assertIn('线性扫描', reply)
        self.assertIn('线性扫描', complete.call_args.args[0][-1]['content'])

    def test_preview_does_not_write_and_confirmation_survives_new_graph(self):
        self.seed()
        with patch('learning_agent.model.complete', return_value=call('generate_study_plan', subject='程序设计')):
            reply = self.run_as('帮我安排复习')
        self.assertIn('尚未修改', reply)
        import re
        code = re.search(r'确认执行 ([a-f0-9]+)', reply)[1]
        self.assertEqual(self.own(self.ua, lambda: study_db.get_plan_by_subject()), [])
        with patch('learning_agent.model.complete', side_effect=AssertionError('confirmation must not call model')):
            saved = self.run_as('确认执行 '+code)
        self.assertIn('已保存', saved)
        self.assertTrue(self.own(self.ua, lambda: study_db.get_plan_by_subject()))
        again = self.run_as('确认执行 '+code)
        self.assertIn('没有匹配', again)

    def test_vague_yes_does_not_approve(self):
        self.seed()
        with patch('learning_agent.model.complete', return_value=call('generate_study_plan')):
            self.run_as('预览计划')
        with patch('learning_agent.model.complete', return_value={'role': 'assistant', 'content': '请明确你的需求。'}):
            self.run_as('好的')
        self.assertEqual(self.own(self.ua, lambda: study_db.get_plan_by_subject()), [])

    def test_stale_proposal_refused(self):
        _, bid = self.seed()
        preview = self.own(self.ua, lambda: proposals.preview_plan())
        self.own(self.ua, lambda: study_db.update_mastery(bid, 90))
        with self.assertRaisesRegex(ValueError, '变化'):
            self.own(self.ua, lambda: proposals.apply(preview))

    def test_preview_commit_is_exact_and_idempotent(self):
        self.seed()
        preview = self.own(self.ua, lambda: proposals.preview_plan())
        first = self.own(self.ua, lambda: proposals.apply(preview))
        second = self.own(self.ua, lambda: proposals.apply(preview))
        self.assertEqual(first, second)
        def actual():
            with study_db.get_conn() as conn:
                return [tuple(r) for r in conn.execute('SELECT date,block_id,task_type,est_minutes,subject_id,resource,paper_ref FROM plans ORDER BY id')]
        self.assertEqual(self.own(self.ua, actual), [tuple(r) for r in preview['rows']])

    def test_quiz_failure_updates_mastery_without_replanning(self):
        _, bid = self.seed()
        def check():
            kit = StudyToolkit()
            kit.record_quiz_score(bid, 20)
            with patch('scheduler.build_plan', side_effect=AssertionError('must not replan')):
                reply = kit.record_quiz_score(bid, 10)
            self.assertIn('当前复习计划未改动', reply)
            self.assertIn('分钟', reply)
        self.own(self.ua, check)

    def test_courseware_context_includes_original_code_and_source(self):
        self.seed()
        self.a.post(existing.PREFIX+'/upload', files={'files': ('课件.txt', '本课使用线性扫描：for i in range(n): 比较相邻元素。'.encode())}, data={'subject': '程序设计'})
        with patch('learning_agent.model.complete', return_value={'role': 'assistant', 'content': '按课件线性扫描。'}) as complete:
            self.run_as('代码用什么思路')
        system = complete.call_args.args[0][0]['content']
        self.assertIn('for i in range(n)', system)
        self.assertIn('课件.txt', system)

    def test_accounts_do_not_share_materials_or_confirmation(self):
        self.seed()
        self.b.patch(existing.STUDY+'/settings', json={'allow_cloud_model': True})
        preview = self.own(self.ua, lambda: proposals.preview_plan())
        self.assertIn('没有匹配', self.run_as('确认执行 '+preview['code'], user=self.ub))
        self.assertEqual(self.own(self.ub, lambda: tools.execute('list_knowledge_blocks', {})), [])

    def test_privacy_off_blocks_model_even_with_checkpoint(self):
        with patch('learning_agent.model.complete', side_effect=AssertionError('no cloud')):
            self.assertIn('未开启', self.run_as('解释这道题'))

    def test_cancelled_request_stops_before_model(self):
        self.seed()
        with patch('learning_agent.model.complete', side_effect=AssertionError('no model')):
            with self.assertRaisesRegex(RuntimeError, '停止'):
                self.run_as('讲解', cancelled=lambda: True)

    def test_learning_path_does_not_initialize_a_plan(self):
        self.seed()
        self.assertIn('确认', self.own(self.ua, lambda: StudyToolkit().generate_learning_path()))
        self.assertEqual(self.own(self.ua, lambda: study_db.get_plan_by_subject()), [])

    def test_invalid_tools_cannot_write(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.own(self.ua, lambda: tools.execute('apply_plan', {}))
        with self.assertRaises(Exception):
            self.own(self.ua, lambda: tools.execute('generate_study_plan', {'daily_minutes': 'bad'}))

    def test_websocket_and_search_use_new_agent(self):
        self.seed()
        with patch('learning_agent.model.complete', return_value={'role': 'assistant', 'content': '独立学习Agent已接入。'}):
            response = self.a.post(existing.STUDY+'/search', json={'content': [{'value': '你好'}]})
        self.assertEqual(response.status_code, 200)
        self.assertIn('独立学习Agent', response.text)

    def test_websocket_emits_real_agent_progress(self):
        self.seed()
        path = 'wss://testserver/api/openans-support-chatbot/v1/robot/wss/messages'
        with patch('learning_agent.model.complete', return_value={'role': 'assistant', 'content': '按课件解答。'}):
            with self.a.websocket_connect(path, headers={'Origin': 'https://testserver'}) as socket:
                socket.send_json({'action': 'message', 'topic': 'new-agent', 'data': json.dumps({
                    'initData': [{'type': 'text', 'value': '讲解线性扫描'}],
                    'sessionInfo': {'learningSessionId': 'new-agent'}})})
                events = []
                while True:
                    event = socket.receive_json()['data']
                    events.append(event)
                    if event['type'] == 'control-status-message':
                        break
        self.assertTrue(any(e.get('engine') == 'agent' for e in events))
        self.assertTrue(any(e['type'] == 'lui-message-manus-step' for e in events))
        self.assertTrue(any(e.get('initData', [{}])[0].get('value') == '按课件解答。' for e in events if isinstance(e.get('initData'), list)))

    def test_date_preview_cannot_change_exam_before_confirmation(self):
        sid, _ = self.seed()
        new_date = (date.today()+timedelta(days=25)).isoformat()
        def preview():
            return tools.execute('set_exam_date', {'subject': '程序设计', 'date_text': new_date})
        proposal = self.own(self.ua, preview)
        self.assertNotEqual(self.own(self.ua, lambda: study_db.get_subject(sid))['exam_date'], new_date)
        self.own(self.ua, lambda: proposals.apply(proposal))
        self.assertEqual(self.own(self.ua, lambda: study_db.get_subject(sid))['exam_date'], new_date)

    def test_time_gap_preview_explains_consequences(self):
        sid, bid = self.seed()
        def preview():
            with study_db.get_conn() as conn:
                conn.execute('UPDATE blocks SET est_minutes=1000 WHERE id=?', (bid,))
                conn.execute('UPDATE subjects SET exam_date=? WHERE id=?', ((date.today()+timedelta(days=1)).isoformat(), sid))
            return proposals.preview_plan(daily_minutes=15)
        proposal = self.own(self.ua, preview)
        self.assertTrue(proposal['gaps'])
        self.assertIn('不能保证考前完成', proposals.describe(proposal))
        self.assertEqual(self.own(self.ua, lambda: study_db.get_plan_by_subject()), [])

    def test_web_search_denied_by_backend_setting(self):
        self.seed()
        with self.assertRaises(PermissionError):
            self.own(self.ua, lambda: tools.execute('web_search', {'query': 'test'}))

    def test_chat_does_not_ocr_an_uncached_pdf_before_answering(self):
        self.seed()
        self.a.post(existing.PREFIX+'/upload', files={'files': ('课件.pdf', b'fake-pdf')}, data={'subject': '程序设计'})
        with patch('material_analyst.extract_pdf_text', side_effect=AssertionError('no eager OCR')), patch(
                'learning_agent.model.complete', return_value={'role': 'assistant', 'content': '你好，需要学习哪一部分？'}):
            reply = self.run_as('你好')
        self.assertIn('你好', reply)

    def test_explicit_material_read_reuses_cache_and_invalidates_changed_file(self):
        self.seed()
        result = self.a.post(existing.PREFIX+'/upload', files={'files': ('课件.pdf', b'fake-pdf')}, data={'subject': '程序设计'})
        identifier = result.json()['data'][0]['upload_id']
        def check():
            from learning_agent.materials import course_context
            from cosight_server.deep_research.services.study_library import material_path
            with patch('material_analyst.extract_pdf_text', return_value='线性扫描原文步骤') as extract:
                self.assertIn('线性扫描', course_context('扫描'))
                self.assertIn('线性扫描', course_context('扫描', cached_only=True))
                self.assertIn('线性扫描', course_context('扫描'))
                self.assertEqual(extract.call_count, 1)
            material_path(identifier).write_bytes(b'changed-pdf-content')
            with patch('material_analyst.extract_pdf_text', side_effect=AssertionError('must defer new PDF')):
                self.assertIn('尚无文本缓存', course_context('扫描', cached_only=True))
        self.own(self.ua, check)

    def test_planning_reads_only_parsed_database_not_original_or_cache(self):
        self.seed()
        with patch('learning_agent.runtime.course_context', side_effect=AssertionError('no source retrieval for scheduling')), patch(
                'learning_agent.model.complete', return_value={'role':'assistant','content':'我会根据已解析知识点预览排期。'}) as complete:
            self.run_as('请生成复习计划')
        system = complete.call_args.args[0][0]['content']
        self.assertIn('prereq_ids',system)
        self.assertIn('线性扫描',system)


if __name__ == '__main__':
    unittest.main()

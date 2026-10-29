"""Parsing button integration: temporary accounts and mocked models only."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_multi_user as existing
import study_db
import scheduler
from cosight_server.deep_research.services import material_parser as parser

BLOCKS = [{'chapter': '第五章', 'name': '导数', 'prereq': [], 'est_minutes': 40}]
PROFILES = [{'block_index': 0, 'q_type': '选择', 'score_value': 5, 'frequency': '课件估计'}]
PAPER = [{'knowledge_point': '导数', 'q_type': '计算', 'stem': '求导', 'answer': '', 'score_value': 10}]


class MaterialParserTests(unittest.TestCase):
    setUp = existing.MultiUserTests.setUp
    tearDown = existing.MultiUserTests.tearDown
    register = existing.MultiUserTests.register
    own = existing.MultiUserTests.own

    def upload(self, name='第五章.txt'):
        response = self.a.post(existing.PREFIX+'/upload', data={'subject': '高等数学'},
            files={'files': (name, b'course material', 'text/plain')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data'][0]['upload_id']

    def parse(self, identifier, kind='courseware', reparse=False):
        return self.a.post(existing.STUDY+f'/materials/{identifier}/parse', json={'kind':kind,'reparse':reparse})

    def enable(self):
        self.a.patch(existing.STUDY+'/settings', json={'allow_cloud_model': True})

    def rows(self, table):
        def read():
            with study_db.get_conn() as conn:
                return [dict(row) for row in conn.execute('SELECT * FROM '+table)]
        return self.own(self.ua, read)

    def test_consent_ownership_and_no_exam_needed(self):
        identifier = self.upload()
        self.assertEqual(self.parse(identifier).status_code,409)
        self.enable()
        self.assertEqual(self.b.post(existing.STUDY+f'/materials/{identifier}/parse',json={'kind':'courseware'}).status_code,404)
        with patch.object(parser,'analyze',return_value=(BLOCKS,PROFILES)):
            self.assertEqual(self.parse(identifier).status_code,202)
        self.assertEqual(self.rows('subjects')[0]['exam_date'],'')
        self.assertEqual(len(self.rows('blocks')),1)
        self.assertEqual(len(self.rows('plans')),0)
        def tasks():
            with study_db.get_conn() as conn:
                return scheduler.collect_tasks(conn,__import__('datetime').date.today())
        self.assertEqual(self.own(self.ua,tasks),{})
        material=self.a.get(existing.STUDY+'/materials').json()['materials'][0]
        self.assertEqual(material['parsing']['status'],'done')
        self.assertIn('1 个知识点',material['parsing']['summary'])
        self.assertEqual(self.a.get(existing.STUDY+'/plan-overview').status_code,200)

    def test_accumulation_reparse_failure_and_mastery_preserved(self):
        self.enable();first=self.upload();second=self.upload('第六章.txt')
        with patch.object(parser,'analyze',return_value=(BLOCKS,PROFILES)):
            self.parse(first)
            other=[{**BLOCKS[0],'name':'积分','chapter':'第六章'}]
            with patch.object(parser,'analyze',return_value=(other,PROFILES)):
                self.parse(second)
            self.assertEqual(len(self.rows('blocks')),2)
            self.assertEqual(self.parse(first).status_code,409)
            bid=self.rows('blocks')[0]['id']
            self.own(self.ua,lambda:study_db.update_mastery(bid,80))
            self.assertEqual(self.parse(first,reparse=True).status_code,202)
        self.assertEqual(len(self.rows('blocks')),2)
        self.assertEqual(self.rows('blocks')[0]['mastery'],56)
        self.assertEqual(self.a.patch(existing.STUDY+f'/materials/{first}',json={'subject':'线性代数'}).status_code,409)
        with patch.object(parser,'analyze',side_effect=ValueError('模型输出达到长度上限')):
            self.parse(first,reparse=True)
        self.assertEqual(len(self.rows('blocks')),2)
        state=next(m for m in self.a.get(existing.STUDY+'/materials').json()['materials'] if m['upload_id']==first)['parsing']
        self.assertEqual(state['status'],'failed');self.assertTrue(state['summary'])

    def test_paper_first_and_reparse_no_duplicate_questions(self):
        self.enable();paper=self.upload('往年真题.txt');course=self.upload()
        with patch.object(parser,'analyze',return_value=(PAPER,[])):
            self.parse(paper,'paper')
            self.parse(paper,'paper',True)
        self.assertEqual(len(self.rows('exam_questions')),1)
        with patch.object(parser,'analyze',return_value=(BLOCKS,PROFILES)):
            self.parse(course)
        self.assertTrue(any(p['source']=='真题资料统计' for p in self.rows('exam_profile')))
        self.assertEqual(len(self.rows('plans')),0)

    def test_real_analysis_validation_with_mock_model(self):
        self.enable()
        def check():
            with patch.object(parser.ma,'extract_pdf_text',return_value='课件内容'*30),patch.object(parser.ma,'sniff_llm_config',return_value={}),patch.object(parser.ma,'chat',side_effect=[json.dumps(BLOCKS),json.dumps(PROFILES)]):
                self.assertEqual(parser.analyze(Path('test.txt'),'courseware','高等数学')[0],BLOCKS)
            invalid=[{**BLOCKS[0],'prereq':[0]}]
            with patch.object(parser.ma,'extract_pdf_text',return_value='课件内容'*30),patch.object(parser.ma,'sniff_llm_config',return_value={}),patch.object(parser.ma,'chat',return_value=json.dumps(invalid)):
                with self.assertRaises(ValueError):parser.analyze(Path('test.txt'),'courseware','高等数学')
        self.own(self.ua,check)

    def test_running_duplicate_and_stale_state(self):
        from fastapi import BackgroundTasks, HTTPException
        self.enable(); identifier=self.upload()
        def check():
            background=BackgroundTasks()
            parser.start(identifier,'courseware',False,background)
            self.assertEqual(parser.states()[identifier]['status'],'running')
            try:
                with self.assertRaises(HTTPException) as error:
                    parser.start(identifier,'courseware',False,BackgroundTasks())
                self.assertEqual(error.exception.status_code,409)
            finally:
                job=background.tasks[0]
                with patch.object(parser,'analyze',return_value=(BLOCKS,PROFILES)):
                    job.func(*job.args,**job.kwargs)
            with parser.connection() as conn:
                conn.execute("UPDATE material_parses SET status='running' WHERE upload_id=?",(identifier,))
            self.assertEqual(parser.states()[identifier]['status'],'failed')
        self.own(self.ua,check)

    def test_correct_parsed_subject_keeps_history_and_other_sources(self):
        self.enable(); first=self.upload(); other=self.upload('第六章.txt')
        with patch.object(parser,'analyze',return_value=(BLOCKS,PROFILES)):
            self.parse(first); self.parse(other)
        bid=self.rows('blocks')[0]['id']
        self.own(self.ua,lambda:study_db.update_mastery(bid,80))
        def plan():
            with study_db.get_conn() as conn:
                conn.execute("INSERT INTO plans(version,date,block_id,subject_id,task_type,est_minutes) VALUES (1,'2026-12-01',?,1,'讲解',40)",(bid,))
        self.own(self.ua,plan)
        before=self.rows('plans')
        url=existing.STUDY+f'/materials/{first}'
        self.assertEqual(self.a.patch(url,json={'subject':'线性代数'}).status_code,409)
        with patch.object(parser,'analyze',side_effect=AssertionError('classification must not call model')):
            result=self.a.patch(url,json={'subject':'线性代数','confirm_reset':True})
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['requires_reparse'])
        self.assertEqual(self.rows('plans'),before)
        old=self.rows('blocks')[0]
        self.assertEqual(old['mastery'],56)
        self.assertNotEqual(old['status'],'已归档')
        materials=self.a.get(existing.STUDY+'/materials').json()['materials']
        corrected=next(m for m in materials if m['upload_id']==first)
        self.assertEqual(corrected['subject'],'线性代数')
        self.assertEqual(corrected['parsing']['status'],'done')
        self.assertEqual(corrected['parsing']['subject'],'线性代数')
        self.assertIn('1 个知识点',corrected['parsing']['summary'])
        self.assertEqual(next(m for m in materials if m['upload_id']==other)['parsing']['status'],'done')
        sid=self.own(self.ua,lambda:study_db.get_subject_id_by_name('线性代数'))
        moved=self.own(self.ua,lambda:study_db.get_blocks_by_subject(sid))
        self.assertEqual(len(moved),1)
        self.assertEqual(moved[0]['id'],bid)
        self.assertEqual(moved[0]['mastery'],56)
        self.assertEqual(self.rows('plans'),before)

    def test_correct_paper_subject_keeps_questions_without_reparse(self):
        self.enable(); identifier=self.upload('真题.txt')
        with patch.object(parser,'analyze',return_value=(PAPER,[])):
            self.parse(identifier,'paper')
        before=self.rows('exam_questions')[0]
        with patch.object(parser,'analyze',side_effect=AssertionError('no model')):
            result=self.a.patch(existing.STUDY+f'/materials/{identifier}',
                json={'subject':'线性代数','confirm_reset':True})
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['requires_reparse'])
        new=self.rows('exam_questions')[0]
        self.assertEqual(new['id'],before['id'])
        self.assertEqual(new['stem'],before['stem'])
        self.assertNotEqual(new['subject_id'],before['subject_id'])
        self.assertEqual(self.own(self.ua,lambda:study_db.get_knowledge_frequency(before['subject_id'])),[])
        self.assertEqual(len(self.own(self.ua,lambda:study_db.get_knowledge_frequency(new['subject_id']))),1)

    def test_correct_paper_kind_excludes_archived_questions_from_frequency(self):
        self.enable(); identifier=self.upload('真题.txt')
        with patch.object(parser,'analyze',return_value=(PAPER,[])):
            self.parse(identifier,'paper')
        self.assertTrue(self.own(self.ua,lambda:study_db.get_knowledge_frequency()))
        result=self.a.patch(existing.STUDY+f'/materials/{identifier}',json={'kind':'courseware','confirm_reset':True})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(self.rows('exam_questions')),1)  # history is retained
        self.assertEqual(self.own(self.ua,lambda:study_db.get_knowledge_frequency()),[])
        self.assertEqual(self.own(self.ua,lambda:study_db.get_exam_questions_by_subject()),[])
        material=self.a.get(existing.STUDY+'/materials').json()['materials'][0]
        self.assertEqual(material['kind'],'courseware')
        self.assertIsNone(material['parsing'])

    def test_correction_is_scoped_and_rolls_back_both_databases(self):
        self.enable(); identifier=self.upload()
        with patch.object(parser,'analyze',return_value=(BLOCKS,PROFILES)):
            self.parse(identifier)
        url=existing.STUDY+f'/materials/{identifier}'
        self.assertEqual(self.b.patch(url,json={'subject':'线性代数','confirm_reset':True}).status_code,404)
        def fail():
            with study_db.get_conn() as conn:
                conn.execute("CREATE TRIGGER fail_correction BEFORE UPDATE ON blocks BEGIN SELECT RAISE(ABORT,'test rollback'); END")
            with self.assertRaises(Exception):
                parser.correct_classification(identifier,{'subject':'线性代数','confirm_reset':True})
        self.own(self.ua,fail)
        material=self.a.get(existing.STUDY+'/materials').json()['materials'][0]
        self.assertEqual(material['subject'],'高等数学')
        self.assertEqual(material['parsing']['status'],'done')
        self.assertNotEqual(self.rows('blocks')[0]['status'],'已归档')


if __name__=='__main__':unittest.main()

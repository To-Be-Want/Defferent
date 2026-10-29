"""Knowledge/source positioning without paid models or original user data."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_multi_user as existing
import study_db
import material_analyst as ma
from learning_agent import source_index as index, material_cache
from learning_agent.materials import course_context
from cosight_server.deep_research.services import material_parser as parser
from cosight_server.deep_research.services.study_library import material_path

BLOCKS = [{'name':'线性扫描','chapter':'循环','prereq':[], 'est_minutes':40}]
PROFILE = [{'block_index':0,'q_type':'选择','score_value':5,'frequency':'课件估计'}]
TEXT = '[第 2 页]\n线性扫描：从左到右依次比较。\n\n例题：寻找连续递增段。\n\n[第 3 页]\n```python\ndef scan(a):\n\n    return len(a)\n```\n'


class SourceIndexTests(unittest.TestCase):
    setUp=existing.MultiUserTests.setUp
    tearDown=existing.MultiUserTests.tearDown
    register=existing.MultiUserTests.register
    own=existing.MultiUserTests.own

    def prepare(self, text=TEXT, name='课件.md'):
        self.a.patch(existing.STUDY+'/settings',json={'allow_cloud_model':True})
        upload=self.a.post(existing.PREFIX+'/upload',files={'files':(name,text.encode())},data={'subject':'程序设计'})
        identifier=upload.json()['data'][0]['upload_id']
        self.own(self.ua,lambda:ma.extract_pdf_text(str(material_path(identifier)),max_chars=120000))
        with patch.object(parser,'analyze',return_value=(BLOCKS,PROFILE)):
            result=self.a.post(existing.STUDY+f'/materials/{identifier}/parse',json={'kind':'courseware'})
        self.assertEqual(result.status_code,202,result.text)
        def block():
            with study_db.get_conn() as conn:
                return conn.execute('SELECT id FROM blocks ORDER BY id LIMIT 1').fetchone()[0]
        return identifier,self.own(self.ua,block)

    def test_offsets_pages_versions_and_direct_sql_retrieval(self):
        identifier,bid=self.prepare()
        with patch.object(material_cache,'read',side_effect=AssertionError('no cache scanning')), patch.object(
                material_cache,'document',side_effect=AssertionError('no cache scanning')),patch.object(
                ma,'extract_pdf_text',side_effect=AssertionError('no OCR')):
            result=self.own(self.ua,lambda:index.lookup(block_id=bid))
        self.assertTrue(result['excerpts'])
        for part in result['excerpts']:
            self.assertEqual(TEXT[part['start_char']:part['end_char']],part['text'])
            self.assertEqual(part['page_number'],2)
            self.assertIn('#page=2',part['source_url'])
            self.assertTrue(part['version']);self.assertTrue(part['chunk_id'])
        self.assertEqual(result['retrieval'],'knowledge_source_links')

    def test_model_references_link_code_and_keep_fenced_block_whole(self):
        identifier,bid=self.prepare()
        def associate():
            with study_db.get_conn() as conn:
                rows=[dict(r) for r in conn.execute('SELECT * FROM material_text_chunks ORDER BY sequence')]
                code=next(r for r in rows if r['content_type']=='代码')
                index.link_blocks(conn,identifier,code['version'],[{**BLOCKS[0],'id':bid,'source_chunk_ids':[code['chunk_id'],'invented-id']}])
            return index.lookup(block_id=bid)
        result=self.own(self.ua,associate)
        code=result['excerpts'][0]
        self.assertEqual(code['page_number'],3)
        self.assertIn('def scan(a):\n\n    return len(a)',code['text'])
        self.assertTrue(code['text'].endswith('```'))
        self.assertEqual(code['match_method'],'model_reference')

    def test_changed_original_invalidates_old_locations_without_ocr(self):
        identifier,bid=self.prepare()
        def check():
            material_path(identifier).write_text('线性扫描：新版本',encoding='utf-8')
            stale=index.lookup(block_id=bid)
            self.assertFalse(stale['excerpts']);self.assertEqual(stale['stale_uploads'],[identifier])
            ma.extract_pdf_text(str(material_path(identifier)))
            index.index_cached(identifier,material_path(identifier))
            with study_db.get_conn() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM material_text_versions').fetchone()[0],2)
                self.assertEqual(conn.execute('SELECT SUM(active) FROM material_text_versions').fetchone()[0],1)
            self.assertIn('新版本',index.lookup(block_id=bid)['excerpts'][0]['text'])
        self.own(self.ua,check)

    def test_native_pdf_retains_real_page_markers_without_ocr(self):
        import fitz
        doc=fitz.open()
        for text in ('First page has enough meaningful text about integration.', 'Second page contains a complete linear scan example.'):
            page=doc.new_page();page.insert_text((50,70),text)
        content=doc.tobytes();doc.close()
        upload=self.a.post(existing.PREFIX+'/upload',files={'files':('native.pdf',content)},data={'subject':'程序设计'})
        identifier=upload.json()['data'][0]['upload_id']
        with patch('app.cosight.tool.ocr_toolkit.OCRToolkit._get_engine',side_effect=AssertionError('no OCR')):
            text=self.own(self.ua,lambda:ma.extract_pdf_text(str(material_path(identifier))))
        self.assertIn('[第 1 页]',text);self.assertIn('[第 2 页]',text)
        self.own(self.ua,lambda:index.index_cached(identifier,material_path(identifier)))
        def pages():
            with study_db.get_conn() as conn:
                return {r[0] for r in conn.execute('SELECT page_number FROM material_text_chunks')}
        self.assertEqual(self.own(self.ua,pages),{1,2})

    def test_legacy_cache_does_not_invent_pdf_page_numbers(self):
        _,bid=self.prepare('线性扫描：旧缓存没有页码。')
        result=self.own(self.ua,lambda:index.lookup(block_id=bid))
        self.assertTrue(result['excerpts']);self.assertIsNone(result['excerpts'][0]['page_number'])
        self.assertNotIn('#page=',result['excerpts'][0]['source_url'])

    def test_sources_and_original_preview_are_account_scoped(self):
        identifier,bid=self.prepare()
        url=existing.STUDY+f'/knowledge-blocks/{bid}/sources'
        self.assertEqual(self.a.get(url).status_code,200)
        self.assertEqual(self.b.get(url).status_code,404)
        source=existing.STUDY+f'/materials/{identifier}/source'
        self.assertEqual(self.a.get(source).status_code,200)
        self.assertEqual(self.b.get(source).status_code,404)
        self.assertEqual(self.guest.get(source).status_code,401)

    def test_subject_change_preserves_link_ids_and_requires_no_index_rebuild(self):
        identifier,bid=self.prepare()
        before=self.own(self.ua,lambda:index.lookup(block_id=bid))['excerpts']
        self.a.patch(existing.STUDY+f'/materials/{identifier}',json={'subject':'数据结构','confirm_reset':True})
        after=self.own(self.ua,lambda:index.lookup(block_id=bid))['excerpts']
        self.assertEqual([x['chunk_id'] for x in before],[x['chunk_id'] for x in after])
        self.assertEqual([x['version'] for x in before],[x['version'] for x in after])

    def test_cached_backfill_is_idempotent_and_keeps_plan_records(self):
        identifier,bid=self.prepare()
        def check():
            with study_db.get_conn() as conn:
                conn.execute('DELETE FROM knowledge_source_links')
            with patch.object(ma,'extract_pdf_text',side_effect=AssertionError('no reparse')):
                index.index_cached(identifier,material_path(identifier))
                index.index_cached(identifier,material_path(identifier))
            with study_db.get_conn() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM material_text_versions').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM plans').fetchone()[0],0)
            self.assertTrue(index.lookup(block_id=bid)['excerpts'])
        self.own(self.ua,check)

    def test_unknown_topic_is_not_falsely_attached_to_arbitrary_text(self):
        identifier,bid=self.prepare()
        def check():
            with study_db.get_conn() as conn:
                version=conn.execute('SELECT version FROM material_text_versions').fetchone()[0]
                index.link_blocks(conn,identifier,version,[{'id':bid,'name':'没有出现的量子知识','chapter':''}])
            return index.lookup(block_id=bid)
        self.assertFalse(self.own(self.ua,check)['excerpts'])

    def test_course_context_prefers_direct_knowledge_locations(self):
        _,bid=self.prepare()
        with patch.object(material_cache,'read',side_effect=AssertionError('no cache scan')):
            value=self.own(self.ua,lambda:course_context('线性扫描',block_id=bid,cached_only=True))
        self.assertEqual(json.loads(value)['retrieval'],'knowledge_source_links')

    def test_parser_requests_and_saves_real_chunk_ids(self):
        identifier,bid=self.prepare()
        def code_id():
            with study_db.get_conn() as conn:
                return conn.execute("SELECT chunk_id FROM material_text_chunks WHERE content_type='代码'").fetchone()[0]
        reference=self.own(self.ua,code_id)
        value=[{**BLOCKS[0],'source_chunk_ids':[reference]}]
        with patch.object(parser.ma,'sniff_llm_config',return_value={}),patch.object(parser.ma,'chat',side_effect=[json.dumps(value),json.dumps(PROFILE)]) as model:
            response=self.a.post(existing.STUDY+f'/materials/{identifier}/parse',json={'kind':'courseware','reparse':True})
        self.assertEqual(response.status_code,202,response.text)
        self.assertIn(reference,model.call_args_list[0].args[0][0]['content'])
        self.assertIn('source_chunk_ids',model.call_args_list[0].args[0][0]['content'])
        result=self.own(self.ua,lambda:index.lookup(block_id=bid))
        self.assertEqual(result['excerpts'][0]['chunk_id'],reference)
        self.assertEqual(result['excerpts'][0]['page_number'],3)


if __name__=='__main__':unittest.main()

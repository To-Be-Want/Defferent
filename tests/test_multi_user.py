"""No external model calls; all test data lives in a disposable temporary directory."""
import concurrent.futures
from datetime import date, timedelta
import json
import os
import sys
import tempfile
import importlib.util
import threading
import time
import socket as socket_module
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from cloud_app import create_app
from user_context import current_user, user_root, scoped_session_id
from cosight_server.deep_research.services import account_service as accounts
import study_db

PREFIX = "/api/nae-deep-research/v1"
STUDY = PREFIX + "/deep-research"


class MultiUserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cosight-accounts-")
        self.environment = patch.dict(os.environ, {"COSIGHT_DATA_DIR": self.directory.name,
            "COSIGHT_REGISTRATION_CODE": "test-invitation", "COSIGHT_COOKIE_SECURE": "true"})
        self.environment.start()
        self.app = create_app()
        self.a = TestClient(self.app, base_url="https://testserver")
        self.b = TestClient(self.app, base_url="https://testserver")
        self.guest = TestClient(self.app, base_url="https://testserver")
        self.ua = self.register(self.a, "alice")
        self.ub = self.register(self.b, "bob")

    def tearDown(self):
        self.a.close(); self.b.close(); self.guest.close()
        self.environment.stop()
        self.directory.cleanup()

    def register(self, client, name):
        response = client.post("/auth/register", json={"username": name, "password": "test-password-1234",
            "invitation_code": "test-invitation"})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response.json()["user"]

    def own(self, user, function):
        token = current_user.set(user)
        try:
            return function()
        finally:
            current_user.reset(token)

    def test_login_and_hashes(self):
        with accounts.connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id=?", (self.ua["id"],)).fetchone()
            self.assertTrue(row[0].startswith("scrypt$"))
            self.assertNotIn("test-password", row[0])
            stored = conn.execute("SELECT token_hash FROM sessions LIMIT 1").fetchone()[0]
            self.assertNotEqual(stored, self.a.cookies.get(accounts.COOKIE_NAME))
        cookie = self.a.get("/auth/me").json()
        self.assertEqual(cookie["user"]["username"], "alice")
        self.assertEqual(self.guest.post("/auth/login", json={"username":"alice", "password":"wrong-password"}).status_code, 401)
        login = self.guest.post("/auth/login", json={"username":"alice", "password":"test-password-1234"})
        self.assertEqual(login.status_code, 200)
        self.assertIn("HttpOnly", login.headers["set-cookie"])
        self.assertIn("Secure", login.headers["set-cookie"])

    def test_guest_csrf_origin_and_invitation(self):
        self.assertEqual(self.guest.get(STUDY + "/materials").status_code, 401)
        self.assertEqual(self.guest.get("/cosight/login.html").status_code, 200)
        self.assertEqual(self.a.patch(STUDY+"/settings", headers={"X-CSRF-Token":"wrong"}, json={}).status_code,403)
        self.assertEqual(self.a.get(STUDY+"/materials", headers={"Origin":"https://evil.example"}).status_code,403)
        self.assertEqual(self.guest.post("/auth/register",json={"username":"charlie","password":"test-password-1234","invitation_code":"wrong"}).status_code,403)
        self.assertEqual(self.guest.get("/docs").status_code,404)

    def test_registration_modes_and_home_redirect(self):
        self.assertEqual(self.guest.get("/cosight/",follow_redirects=False).status_code,303)
        self.assertEqual(self.guest.get("/auth/config").json()["registration_mode"],"invite")
        with patch.dict(os.environ,{"COSIGHT_REGISTRATION_MODE":"closed"}):
            response=self.guest.post("/auth/register",json={"username":"closed-user", "password":"test-password-1234", "invitation_code":"test-invitation"})
            self.assertEqual(response.status_code,403)
        with patch.dict(os.environ,{"COSIGHT_REGISTRATION_MODE":"open"}):
            self.assertEqual(self.guest.get("/auth/config").json()["registration_mode"],"open")
            response=self.guest.post("/auth/register",json={"username":"new-user", "password":"test-password-1234"})
            self.assertEqual(response.status_code,200,response.text)
        with patch.dict(os.environ,{"COSIGHT_REGISTRATION_MODE":"invalid"}):
            self.assertEqual(self.guest.get("/auth/config").json()["registration_mode"],"closed")

    def test_logout_revokes_stolen_cookie(self):
        cookie = self.a.cookies.get(accounts.COOKIE_NAME)
        self.assertEqual(self.a.post("/auth/logout").status_code,200)
        self.guest.cookies.set(accounts.COOKIE_NAME,cookie)
        self.assertEqual(self.guest.get("/auth/me").status_code,401)

    def test_user_files_and_subject_classification(self):
        upload = self.a.post(PREFIX+"/upload",files={"files":("notes.txt",b"alice-private")},data={"subject":"通信原理"})
        self.assertEqual(upload.status_code,200,upload.text)
        item = upload.json()["data"][0]
        self.assertEqual(self.a.get(item["url"]).content,b"alice-private")
        self.assertEqual(self.b.get(item["url"]).status_code,404)
        self.assertEqual(self.b.get(STUDY+"/materials").json()["materials"],[])
        self.assertEqual(self.b.patch(STUDY+"/materials/"+item["upload_id"],json={"subject":"高数"}).status_code,404)
        listed = self.a.get(STUDY+"/materials").json()["materials"][0]
        self.assertEqual(listed["subject"],"通信原理")
        self.assertNotIn("file_path",listed)
        other = self.a.post(PREFIX+"/upload",files={"files":("notes.txt",b"second-file")})
        self.assertNotEqual(other.json()["data"][0]["upload_id"],item["upload_id"])
        self.assertEqual(self.a.get(item["url"]).content,b"alice-private")
        self.assertEqual(self.a.post(PREFIX+"/upload",files={"files":("bad.html",b"<script>alert(1)</script>")}).status_code,415)

    def test_paths_and_quiz_are_private(self):
        from app.cosight.tool.study_toolkit import _resolve
        with self.assertRaises(PermissionError):
            self.own(self.ua,lambda:_resolve(str(Path(self.directory.name)/"accounts.db")))
        self.assertEqual(self.a.get(PREFIX+"/upload_files/%2e%2e/accounts.db").status_code,404)
        def make_quiz():
            with study_db.get_conn() as conn:
                conn.execute("INSERT INTO quizzes(token,kind) VALUES ('private-quiz','随堂')")
        self.own(self.ua,make_quiz)
        self.assertEqual(self.a.get("/quiz/private-quiz").status_code,200)
        self.assertEqual(self.b.get("/quiz/private-quiz").status_code,404)
        self.assertEqual(self.guest.get("/quiz/private-quiz",follow_redirects=False).status_code,303)

    def test_learning_data_settings_and_tasks_are_isolated(self):
        self.own(self.ua,lambda:study_db.add_subject("高数","2026-11-01"))
        self.own(self.ub,lambda:study_db.add_subject("英语","2026-11-02"))
        self.assertEqual(self.a.get(STUDY+"/study-schedule").json()["subjects"][0]["name"],"高数")
        self.assertEqual(self.b.get(STUDY+"/study-schedule").json()["subjects"][0]["name"],"英语")
        self.assertFalse(self.a.get(STUDY+"/settings").json()["settings"]["allow_cloud_model"])
        self.a.patch(STUDY+"/settings",json={"allow_cloud_model":True})
        self.assertFalse(self.b.get(STUDY+"/settings").json()["settings"]["allow_cloud_model"])
        self.assertEqual(self.a.patch(STUDY+"/settings",json={"allow_cloud_model":"false"}).status_code,400)
        self.assertEqual(self.a.get(STUDY+"/today-plan?date=bad").status_code,400)

    def test_chat_history_and_server_identity(self):
        def reply(query,**kwargs):
            self.assertTrue(kwargs["session_id"].startswith(current_user.get()["id"]+":"))
            return current_user.get()["username"]+"-reply"
        with patch("study_fast_channel.try_fast_channel",side_effect=reply):
            for client in (self.a,self.b):
                response=client.post(STUDY+"/search",json={"content":[{"type":"text","value":"今天学什么"}],
                    "sessionInfo":{"sessionId":"shared-client-id","user_id":self.ua["id"]}})
                self.assertEqual(response.status_code,200,response.text)
        a=self.a.get(STUDY+"/conversations/shared-client-id").json()["messages"]
        b=self.b.get(STUDY+"/conversations/shared-client-id").json()["messages"]
        self.assertEqual(a[-1]["content"],"alice-reply")
        self.assertEqual(b[-1]["content"],"bob-reply")
        self.assertEqual(len(self.a.get(STUDY+"/conversations").json()["conversations"]),1)

    def test_context_survives_threadpool_without_global_db_mutation(self):
        original=study_db.DB_PATH
        def worker(user):
            return self.own(user,lambda:(study_db.add_subject(user["username"],"2026-12-01"),str(user_root())))
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results=list(pool.map(worker,[self.ua,self.ub]))
        self.assertNotEqual(results[0][1],results[1][1])
        self.assertEqual(study_db.DB_PATH,original)
        self.assertIsNone(current_user.get())

    def test_websocket_auth_origin_and_shared_topic(self):
        from starlette.websockets import WebSocketDisconnect
        path="wss://testserver/api/openans-support-chatbot/v1/robot/wss/messages?lang=zh"
        with self.assertRaises(WebSocketDisconnect):
            with self.guest.websocket_connect(path,headers={"Origin":"https://testserver"}): pass
        with self.assertRaises(WebSocketDisconnect):
            with self.a.websocket_connect(path,headers={"Origin":"https://evil.example"}): pass
        with patch("study_fast_channel.try_fast_channel",side_effect=lambda *a,**kw:current_user.get()["username"]):
            for client,name in ((self.a,"alice"),(self.b,"bob")):
                with client.websocket_connect(path,headers={"Origin":"https://testserver"}) as socket:
                    socket.send_json({"action":"message","topic":"same-topic","data":json.dumps({
                        "initData":[{"type":"text","value":"今天学什么"}],"sessionInfo":{"learningSessionId":"same-session"}})})
                    self.assertEqual(socket.receive_json()["data"]["from"],"human")
                    self.assertEqual(socket.receive_json()["data"]["type"],"study-engine-route")
                    self.assertEqual(socket.receive_json()["data"]["initData"][0]["value"],name)
                    socket.receive_json()

    def test_cloud_disabled_makes_no_external_request(self):
        with patch("requests.post",side_effect=AssertionError("network must not be called")):
            response=self.a.post(STUDY+"/search",json={"content":[{"type":"text","value":"今天学什么"}]})
            self.assertEqual(response.status_code,200,response.text)

    def test_notebook_offline_crud_filter_and_isolation(self):
        api=STUDY+"/mistakes"
        with patch("requests.post",side_effect=AssertionError("no cloud requests")):
            created=self.a.post(api,json={"subject":"高等数学","question":"求极限 sin(x)/x", "correct_answer":"1", "explanation":"基本极限"})
            self.assertEqual(created.status_code,200,created.text)
            mid=created.json()["id"]
            self.assertEqual(self.a.get(api).json()["summary"]["total"],1)
            self.assertEqual(self.b.get(api).json()["items"],[])
            self.assertEqual(self.a.get(api,params={"subject":"高等数学","q":"极限","status":"pending"}).json()["total"],1)
            self.assertEqual(self.a.get(api,params={"q":"不存在"}).json()["total"],0)
            self.assertEqual(self.guest.get(api).status_code,401)
            self.assertEqual(self.b.patch(f"{api}/{mid}",json={"mastered":True}).status_code,404)
            self.assertEqual(self.b.post(f"{api}/{mid}/reviews",json={"is_correct":True}).status_code,404)
            self.assertEqual(self.a.post(f"{api}/{mid}/reviews",json={"is_correct":"false"}).status_code,422)
            for expected in (0,1):
                review=self.a.post(f"{api}/{mid}/reviews",json={"is_correct":True})
                self.assertEqual(review.json()["mastered"],expected)
            self.assertEqual(self.a.get(api,params={"status":"mastered"}).json()["total"],1)
            self.assertEqual(self.a.post(f"{api}/{mid}/reviews",json={"is_correct":False}).json()["mastered"],0)
            self.assertEqual(self.a.patch(f"{api}/{mid}",json={"mastered":True}).status_code,200)
            self.assertEqual(self.a.get(api).json()["summary"]["mastered"],1)
            self.assertEqual(self.a.post(api,json={"question":" ","correct_answer":" "}).status_code,400)

    def test_view_mistakes_chat_bypasses_cloud_gate(self):
        self.a.post(STUDY+"/mistakes",json={"subject":"高等数学","question":"极限例题", "correct_answer":"1"})
        with patch("requests.post",side_effect=AssertionError("no network")), patch("material_analyst.sniff_llm_config",side_effect=AssertionError("no model config lookup")):
            for allowed in (False,True):
                self.a.patch(STUDY+"/settings",json={"allow_cloud_model":allowed})
                response=self.a.post(STUDY+"/search",json={"content":[{"type":"text","value":"查看错题"}],"sessionInfo":{"sessionId":"mistake-offline"}})
                self.assertEqual(response.status_code,200,response.text)
                text=json.loads(response.text)["content"][0]["value"]
                self.assertIn("高等数学",text)
                self.assertIn("/cosight/mistakes.html",text)
                self.assertNotIn("已关闭在线模型",text)
                followup=self.a.post(STUDY+"/search",json={"content":[{"type":"text","value":"高等数学"}],"sessionInfo":{"sessionId":"mistake-offline"}})
                self.assertEqual(followup.status_code,200,followup.text)
                self.assertIn("极限例题",followup.text)
            other=self.b.post(STUDY+"/search",json={"content":[{"type":"text","value":"查看错题"}]})
            self.assertNotIn("极限例题",other.text)
            self.assertIn("空",other.text)

    def test_expiry_rate_limit_and_restart(self):
        token=self.a.cookies.get(accounts.COOKIE_NAME)
        restarted=TestClient(create_app(),base_url="https://testserver")
        try:
            restarted.cookies.set(accounts.COOKIE_NAME,token)
            self.assertEqual(restarted.get("/auth/me").status_code,200)
        finally:
            restarted.close()
        for _ in range(3): self.assertTrue(accounts.allow_attempt("unit-limit",limit=3))
        self.assertFalse(accounts.allow_attempt("unit-limit",limit=3))
        with accounts.connect() as conn:
            conn.execute("UPDATE sessions SET expires_at=0 WHERE user_id=?",(self.ua["id"],))
        self.assertEqual(self.a.get("/auth/me").status_code,401)

    def test_task_completion_and_schedule_changes(self):
        def seed():
            sid=study_db.add_subject("高数","2026-12-01")
            with study_db.get_conn() as conn:
                cursor=conn.execute("INSERT INTO plans(version,date,subject_id,task_type,est_minutes) VALUES (1,date('now','localtime'),?,'复习',30)",(sid,))
                return cursor.lastrowid,sid
        task,sid=self.own(self.ua,seed)
        self.assertEqual(self.b.patch(STUDY+f"/tasks/{task}",json={"status":"完成"}).status_code,404)
        self.assertEqual(self.a.patch(STUDY+f"/tasks/{task}",json={"status":"完成"}).status_code,200)
        self.assertEqual(self.a.get(STUDY+"/today-plan").json()["completed_count"],1)
        self.assertFalse(self.b.get(STUDY+"/today-plan").json()["found"])
        response=self.a.patch(STUDY+"/study-schedule",json={"subjects":[{"id":sid,"exam_date":"2026-12-02"}],
            "study_time_slots":[{"start":"19:00","end":"20:00"}]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["default_daily_minutes"],60)
        self.assertEqual(self.b.get(STUDY+"/study-schedule").json()["subjects"],[])

    def test_exam_lifecycle_creation_validation_and_scheduler(self):
        from datetime import date, timedelta
        future = (date.today()+timedelta(days=20)).isoformat()
        past = (date.today()-timedelta(days=2)).isoformat()
        slots = [{"start":"19:00","end":"20:00"}]
        def save(subjects):
            return self.a.patch(STUDY+"/study-schedule",json={"subjects":subjects,"study_time_slots":slots})
        created=save([{"name":"通信原理","exam_date":future}])
        self.assertEqual(created.status_code,200,created.text)
        course=created.json()["subjects"][0]
        sid=course["id"]
        self.assertEqual(course["display_status"],"pending")
        self.assertIsNone(created.json()["replan_version"])  # Calendar-only exams create no fake tasks.
        self.assertEqual(self.b.get(STUDY+"/study-schedule").json()["subjects"],[])
        rejected=save([{"id":sid,"exam_date":past},{"name":"通信原理","exam_date":future}])
        self.assertEqual(rejected.status_code,400)
        self.assertEqual(self.a.get(STUDY+"/study-schedule").json()["subjects"][0]["exam_date"],future)
        self.assertEqual(save([{"id":sid,"exam_date":future,"exam_status":"invalid"}]).status_code,400)
        def seed():
            block=study_db.add_block(sid,"调制","幅度调制")
            paper=study_db.save_past_paper(sid,"通信真题")
            study_db.save_exam_questions(paper,sid,[{"knowledge_point":"调制","stem":"测试题"}])
            return block
        self.own(self.ua,seed)
        def build():
            import scheduler
            return scheduler.build_plan(verbose=False)
        self.own(self.ua,build)
        def legacy_today():
            with study_db.get_conn() as conn:
                version=conn.execute("SELECT MAX(version) FROM plans").fetchone()[0]
                conn.execute("INSERT INTO plans(version,date,subject_id,task_type) VALUES (?,?,?,'复习')",(version,date.today().isoformat(),sid))
        self.own(self.ua,legacy_today)
        self.assertTrue(self.a.get(STUDY+"/today-plan").json()["found"])
        completed=save([{"id":sid,"exam_date":future,"exam_status":"completed"}])
        self.assertEqual(completed.status_code,200)
        self.assertEqual(completed.json()["subjects"][0]["display_status"],"completed")
        self.assertFalse(self.a.get(STUDY+"/today-plan").json()["found"])
        self.assertEqual(self.own(self.ua,lambda:study_db.get_plan_by_subject(subject_id=sid)),[])
        def current_path():
            import scheduler
            return scheduler.generate_learning_path(subject_id=sid)
        self.assertIsNone(self.own(self.ua,current_path)["learning_path"])
        with self.assertRaises(RuntimeError): self.own(self.ua,build)
        overdue=save([{"id":sid,"exam_date":past,"exam_status":"pending"}])
        self.assertEqual(overdue.json()["subjects"][0]["display_status"],"overdue")
        with self.assertRaisesRegex(RuntimeError,"确认"): self.own(self.ua,build)
        restored=save([{"id":sid,"exam_date":future,"exam_status":"pending"}])
        self.assertIsNone(restored.json()["replan_version"])
        self.assertIn("未修改", restored.json()["replan_message"])
        self.assertEqual(restored.json()["subjects"][0]["block_count"],1)

    def test_old_exam_schema_migrates_without_reclassifying_dates(self):
        def legacy():
            sid=study_db.add_subject("旧课程","2020-01-01")
            with study_db.get_conn() as conn:
                conn.execute("ALTER TABLE subjects DROP COLUMN exam_status")
            result=study_db.get_subject_overview()[0]
            self.assertEqual(result["id"],sid)
            self.assertEqual(result["exam_status"],"pending")
            self.assertEqual(result["display_status"],"overdue")
        self.own(self.ua,legacy)

    def test_subjective_quiz_requires_consent_without_saving_score(self):
        def seed():
            with study_db.get_conn() as conn:
                quiz=conn.execute("INSERT INTO quizzes(token,kind) VALUES ('subjective','随堂')").lastrowid
                conn.execute("INSERT INTO questions(quiz_id,q_type,stem,answer) VALUES (?,'简答','题目','参考答案')",(quiz,))
        self.own(self.ua,seed)
        response=self.a.post("/quiz/subjective/submit",json={"answers":{"1":"解答"}})
        self.assertEqual(response.status_code,409)
        def verify():
            with study_db.get_conn() as conn:
                return conn.execute("SELECT score,submitted_at FROM quizzes WHERE token='subjective'").fetchone()
        row=self.own(self.ua,verify)
        self.assertIsNone(row["score"])
        self.assertIsNone(row["submitted_at"])

    @unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright未安装，跳过浏览器验收")
    def test_browser_login_refresh_upload_and_logout(self):
        import uvicorn
        from playwright.sync_api import sync_playwright
        with patch.dict(os.environ, {"COSIGHT_COOKIE_SECURE":"false"}):
            listener = socket_module.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(self.app, log_level="error"))
            thread = threading.Thread(target=server.run, kwargs={"sockets":[listener]}, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    page = browser.new_page(viewport={"width":1440,"height":1000})
                    failures = []
                    page.on("pageerror", lambda error: failures.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}/cosight/login.html")
                    page.wait_for_function("!document.getElementById('registration-notice').textContent.includes('正在')")
                    screenshots=Path(__file__).resolve().parents[1]/"artifacts"
                    screenshots.mkdir(exist_ok=True)
                    page.screenshot(path=str(screenshots/"login-desktop.png"),full_page=True)
                    page.fill("#username","alice")
                    page.fill("#password","test-password-1234")
                    page.click("#submit")
                    page.wait_for_url("**/cosight/")
                    page.wait_for_function("document.getElementById('home-task-count').textContent.includes('0')")
                    self.assertEqual(page.locator("#account-name").inner_text(),"alice")
                    page.wait_for_function("document.getElementById('cloud-model-status').textContent.includes('未开启')")
                    page.click("#header-model-settings")
                    page.check("#setting-cloud-model")
                    page.once("dialog",lambda dialog:dialog.accept())
                    page.click("#study-settings-save")
                    page.wait_for_function("document.getElementById('cloud-model-status').textContent.includes('已开启')")
                    page.click("#header-model-settings")
                    page.uncheck("#setting-cloud-model")
                    page.once("dialog",lambda dialog:dialog.accept())
                    page.click("#study-settings-save")
                    page.wait_for_function("document.getElementById('cloud-model-status').textContent.includes('未开启')")
                    for viewport_width in (1440,1024,820,768,640,390,320):
                        page.set_viewport_size({"width":viewport_width,"height":1000})
                        page.evaluate("document.getElementById('account-name').textContent='long-account-name-for-layout-testing-0123456789@example.com'")
                        collision=page.evaluate("""() => {
                          const nodes=[document.querySelector('.header h1'),...document.querySelector('.header-actions').children];
                          const boxes=nodes.map(node=>node.getBoundingClientRect()).filter(b=>b.width && b.height);
                          for(let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) {
                            const a=boxes[i],b=boxes[j];
                            if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1) return true;
                          }
                          return false;
                        }""")
                        self.assertFalse(collision,f"Header overlaps at width {viewport_width}")
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),viewport_width+2)
                    page.evaluate("document.getElementById('account-name').textContent='alice'")
                    page.set_viewport_size({"width":1440,"height":1000})
                    page.screenshot(path=str(screenshots/"purple-home-dashboard.png"),full_page=True)
                    self.assertTrue(page.evaluate("document.querySelector('.study-focus-banner').getBoundingClientRect().top > document.querySelector('.initial-input-field-container').getBoundingClientRect().bottom"))
                    page.click('#open-study-schedule')
                    page.wait_for_selector('#study-schedule-dialog[open]')
                    self.assertEqual(page.locator('#study-schedule-dialog').evaluate('(node)=>getComputedStyle(node).borderTopLeftRadius'),'22px')
                    self.assertLess(page.locator('#study-schedule-dialog').bounding_box()['width'],700)
                    self.assertIn('Microsoft YaHei',page.locator('#study-schedule-dialog').evaluate('(node)=>getComputedStyle(node).fontFamily'))
                    for name, exam in (("浏览器考试",date.today()+timedelta(days=30)),("过期考试",date.today()-timedelta(days=2))):
                        page.click('#schedule-add-exam')
                        row=page.locator('#schedule-subjects .schedule-exam-row').last
                        row.locator('.schedule-exam-name').fill(name)
                        row.locator('.schedule-exam-date').fill(exam.isoformat())
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.click('#schedule-save')
                    page.wait_for_selector('#study-schedule-dialog',state='detached')
                    page.click('#open-study-schedule')
                    expired=page.locator('#schedule-subjects .schedule-exam-row').filter(has_text='过期考试')
                    self.assertIn('待确认',expired.inner_text())
                    expired.locator('.exam-toggle').click()
                    self.assertEqual(page.locator('#schedule-completed-list .schedule-exam-row').count(),1)
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.click('#schedule-save')
                    page.wait_for_selector('#study-schedule-dialog',state='detached')
                    page.click('#open-study-schedule')
                    page.click('#schedule-completed summary')
                    self.assertIn('过期考试',page.locator('#schedule-completed-list').inner_text())
                    page.screenshot(path=str(screenshots/'exam-schedule-desktop.png'),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    self.assertLessEqual(page.locator('#study-schedule-dialog').evaluate('(node)=>node.scrollWidth'),365)
                    page.screenshot(path=str(screenshots/'exam-schedule-mobile.png'),full_page=True)
                    page.keyboard.press('Escape')
                    page.wait_for_selector('#study-schedule-dialog',state='detached')
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.click('.sidebar-navigation [data-open-control="open-today-plan"]')
                    page.wait_for_function("document.getElementById('study-quick-panel').getAttribute('aria-hidden') === 'false'")
                    page.click("#close-study-quick")
                    page.click(".notebook-header-link")
                    page.wait_for_url("**/cosight/mistakes.html")
                    page.wait_for_selector(".notebook-empty")
                    page.click("#add-mistake")
                    page.fill("#new-mistake-subject","高等数学")
                    page.fill("#new-mistake-question","极限例题 <script>window.notebookAttack=1</script>")
                    page.fill("#new-mistake-answer","参考答案1")
                    page.click("#save-new-mistake")
                    page.wait_for_selector(".mistake-card")
                    self.assertIsNone(page.evaluate("window.notebookAttack || null"))
                    self.assertFalse(page.get_by_text("参考答案1",exact=True).is_visible())
                    page.locator(".mistake-card summary").click()
                    self.assertTrue(page.get_by_text("参考答案1",exact=True).is_visible())
                    page.locator('[data-review-result="true"]').click()
                    page.wait_for_function("document.querySelector('.mistake-meta').textContent.includes('重做 1 次')")
                    page.screenshot(path=str(screenshots/"mistake-notebook.png"),full_page=True)
                    page.click("nav a")
                    page.wait_for_url("**/cosight/")
                    page.evaluate("localStorage.setItem('cosight:learningSessionId','browser-refresh-session')")
                    page.reload()
                    page.wait_for_function("!document.documentElement.classList.contains('auth-pending')")
                    self.assertEqual(page.evaluate("localStorage.getItem('cosight:learningSessionId')"),"browser-refresh-session")
                    page.select_option("#initial-material-subject",label="自定义科目")
                    page.once("dialog",lambda dialog: dialog.accept("通信原理"))
                    page.set_input_files("#initial-file-input",{"name":"lecture.txt","mimeType":"text/plain","buffer":b"private lecture notes"})
                    page.wait_for_selector(".uploaded-file-item")
                    page.click("#open-material-library")
                    page.wait_for_selector(".material-reclassify")
                    self.assertEqual(page.locator(".material-reclassify").input_value(),"通信原理")
                    page.click("#close-study-quick")
                    # Markup is sanitized, not executed, while Markdown formatting is retained.
                    sanitized=page.evaluate("window.safeMarkdown('**safe**<img src=x onerror=alert(1)><script>alert(1)</script>')")
                    self.assertNotIn("onerror",sanitized)
                    self.assertNotIn("<script",sanitized)
                    self.assertIn("<strong>safe</strong>",sanitized)
                    page.fill("#initial-message-input","今天学什么")
                    page.click("#initial-send-button")
                    page.wait_for_selector(".middle-container.show")
                    page.wait_for_selector("#chat-panel:not(.collapsed)")
                    page.wait_for_selector("#chat-messages .chat-msg.ai")
                    preview_box=page.locator("#dag-preview").bounding_box()
                    chat_box=page.locator("#chat-panel").bounding_box()
                    self.assertGreater(chat_box["width"],preview_box["width"]*2)
                    self.assertGreaterEqual(preview_box["x"],chat_box["x"]+chat_box["width"])
                    sample={"content":{"title":"布局测试", "steps":["整理资料","安排复习","完成练习"],
                        "dependencies":{"2":[1],"3":[2]},"step_statuses":{}}}
                    self.assertEqual(page.evaluate("data => {let failure='';const old=console.error;console.error=(...args)=>{failure=args.map(String).join(' ');old(...args);};const ok=createDag(data);console.error=old;return ok || failure;}",sample), True)
                    page.wait_for_selector("#dag-svg .node")
                    page.wait_for_function("""() => {
                      const box = document.getElementById('dag-svg').getBoundingClientRect();
                      return [...document.querySelectorAll('#dag-svg .node')].every(node => {
                        const b = node.getBoundingClientRect();
                        return b.left >= box.left && b.right <= box.right && b.top >= box.top && b.bottom <= box.bottom;
                      });
                    }""")
                    page.screenshot(path=str(screenshots/"study-workspace-compact-dag.png"),full_page=True)
                    page.click("#dag-preview-expand")
                    self.assertEqual(page.locator("#dag-preview-expand").get_attribute("aria-expanded"),"true")
                    self.assertGreater(page.locator("#dag-preview").bounding_box()["width"],1100)
                    page.keyboard.press("Escape")
                    self.assertEqual(page.locator("#dag-preview-expand").get_attribute("aria-expanded"),"false")
                    page.set_viewport_size({"width":390,"height":844})
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),392)
                    self.assertFalse(page.locator("#dag-svg").is_visible())
                    self.assertTrue(page.locator("#chat-panel").is_visible())
                    chat_box=page.locator("#chat-panel").bounding_box()
                    input_box=page.locator(".dag-container>.input").bounding_box()
                    self.assertGreaterEqual(input_box["y"],chat_box["y"]+chat_box["height"]-1)
                    self.assertLessEqual(input_box["y"]+input_box["height"],846)
                    page.screenshot(path=str(screenshots/"study-workspace-mobile.png"),full_page=True)
                    page.click("#dag-preview-expand")
                    self.assertTrue(page.locator("#dag-svg").is_visible())
                    page.keyboard.press("Escape")
                    page.click("#account-logout")
                    page.wait_for_url("**/cosight/login.html")
                    with patch.dict(os.environ,{"COSIGHT_REGISTRATION_MODE":"open"}):
                        page.reload()
                        page.wait_for_function("document.getElementById('registration-notice').textContent.includes('无需邀请码')")
                        page.click("#register-tab")
                        self.assertFalse(page.locator("#invitation").is_visible())
                        page.fill("#username","browser-new-user")
                        page.fill("#password","browser-password-123")
                        page.fill("#confirm-password","different-password")
                        page.click("#submit")
                        self.assertIn("不一致",page.locator("#account-error").inner_text())
                        page.fill("#confirm-password","browser-password-123")
                        page.screenshot(path=str(screenshots/"register-mobile.png"),full_page=True)
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),392)
                        page.click("#submit")
                        page.wait_for_url("**/cosight/")
                        page.wait_for_function("document.getElementById('account-name').textContent === 'browser-new-user'")
                    self.assertFalse(failures,failures)
                    browser.close()
            finally:
                server.should_exit=True
                thread.join(timeout=10)
                listener.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Disposable import/rollback-safety tests; never use the real project's databases."""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import study_db
from user_context import current_user, user_root
from cosight_server.deep_research.services import account_service as accounts
from cosight_server.deep_research.services.study_library import list_materials, material_path
from study_preferences import get_preferences, update_preferences
from tools.import_legacy_account import import_legacy


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cosight-import-")
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.storage = self.project / "data"
        self.env = patch.dict(os.environ, {"COSIGHT_DATA_DIR": str(self.storage)})
        self.env.start()
        self.user = accounts.create_user("import-user", "test-password-1234")
        self.db_patch = patch.object(study_db, "DB_PATH", str(self.project / "study.db"))
        self.db_patch.start()
        study_db.init_db()
        sid = study_db.add_subject("离散数学", "2026-12-01")
        bid = study_db.add_block(sid, "图论", "最短路", "", 60)
        study_db.save_mistake(bid, "题目", "", "答案", "解析", "离散数学")
        uploads = self.project / "upload_files"
        uploads.mkdir()
        self.source = uploads / "离散数学.pdf"
        self.source.write_bytes(b"private pdf fixture")
        (uploads / "duplicate.pdf").write_bytes(self.source.read_bytes())
        study_db.save_past_paper(sid, "旧试卷", file_path=str(self.source))
        self.source_hash = hashlib.sha256((self.project / "study.db").read_bytes()).hexdigest()
        token = current_user.set(self.user)
        try:
            with study_db.get_conn() as conn:
                conn.execute("CREATE TABLE chat_messages(id INTEGER PRIMARY KEY,content TEXT)")
                conn.execute("INSERT INTO chat_messages VALUES (1,'keep existing conversation')")
            update_preferences({"allow_cloud_model": False})
            self.target = user_root()
        finally:
            current_user.reset(token)

    def tearDown(self):
        self.db_patch.stop()
        self.env.stop()
        self.temporary.cleanup()

    def test_preview_then_import_preserves_originals_and_chat(self):
        preview = import_legacy(self.project, self.storage, self.user["id"])
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["unique_materials"], 1)
        self.assertFalse((self.target / "legacy-import.json").exists())
        result = import_legacy(self.project, self.storage, self.user["id"], apply=True)
        self.assertTrue(result["applied"])
        self.assertTrue((Path(result["backup"]) / "target-study.db").is_file())
        self.assertEqual(hashlib.sha256((self.project / "study.db").read_bytes()).hexdigest(), self.source_hash)
        self.assertTrue(self.source.is_file())
        token = current_user.set(self.user)
        try:
            self.assertEqual(len(study_db.get_mistakes()), 1)
            self.assertFalse(get_preferences()["allow_cloud_model"])
            files = list_materials()
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["filename"], "离散数学.pdf")
            self.assertTrue(material_path(files[0]["upload_id"]).resolve().is_relative_to(self.target / "upload_files"))
            with study_db.get_conn() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0], 1)
                self.assertTrue(Path(conn.execute("SELECT file_path FROM past_papers").fetchone()[0]).is_relative_to(self.target))
        finally:
            current_user.reset(token)
        with self.assertRaises(ValueError):
            import_legacy(self.project, self.storage, self.user["id"], apply=True)

    def test_nonempty_account_is_never_overwritten(self):
        token = current_user.set(self.user)
        try:
            study_db.save_mistake(None, "existing", "", "keep me")
        finally:
            current_user.reset(token)
        with self.assertRaisesRegex(ValueError, "已有学习数据"):
            import_legacy(self.project, self.storage, self.user["id"], apply=True)
        self.assertFalse((self.storage / "backups").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

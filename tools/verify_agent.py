"""Repeatable acceptance suite for the new entry point (no live model calls)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from test_multi_user import MultiUserTests
from test_student_experience import StudentExperienceTests
from test_plans import PlanTests

suite = unittest.TestSuite()
loader = unittest.defaultTestLoader
for module in ('test_learning_agent', 'test_material_parser', 'test_ocr', 'test_source_index'):
    suite.addTests(loader.loadTestsFromName(module))
for name in ('test_login_and_hashes', 'test_guest_csrf_origin_and_invitation',
             'test_registration_modes_and_home_redirect', 'test_logout_revokes_stolen_cookie',
             'test_user_files_and_subject_classification', 'test_paths_and_quiz_are_private',
             'test_learning_data_settings_and_tasks_are_isolated',
             'test_context_survives_threadpool_without_global_db_mutation',
             'test_notebook_offline_crud_filter_and_isolation', 'test_expiry_rate_limit_and_restart',
             'test_task_completion_and_schedule_changes', 'test_exam_lifecycle_creation_validation_and_scheduler',
             'test_old_exam_schema_migrates_without_reclassifying_dates',
             'test_subjective_quiz_requires_consent_without_saving_score'):
    suite.addTest(MultiUserTests(name))
for name in ('test_quiz_without_blocks_and_private_answers', 'test_truncated_quiz_not_saved',
             'test_subject_content_classification_and_manual_priority',
             'test_task_parse_previews_only_and_no_cloud_fallback', 'test_task_parse_nullable_duration_and_warnings',
             'test_reasoning_never_used_as_answer', 'test_photo_answer_is_scoped_and_requires_confirmation'):
    suite.addTest(StudentExperienceTests(name))
for name in ('test_validation_and_csrf', 'test_overview_uses_each_subject_latest_and_preserves_manual_on_replan'):
    suite.addTest(PlanTests(name))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())

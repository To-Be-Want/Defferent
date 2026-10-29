# -*- coding: utf-8 -*-
"""
study_agent_skill.py —— 复习智能体技能注册（阶段4）
放置位置：D:\\Co-Sight\\app\\cosight\\agent\\actor\\instance\\study_agent_skill.py
按 Co-Sight 规范定义技能"说明书"（给大模型看的），真函数在 study_toolkit.py。
"""

from app.agent_dispatcher.infrastructure.entity.SkillFunction import SkillFunction


def parse_study_material_skill():
    return {
        'skill_name': 'parse_study_material',
        'skill_type': "function",
        'display_name_zh': '解析学习资料',
        'display_name_en': 'Parse Study Material',
        'description_zh': '解析用户上传的课件PDF和往年卷PDF，自动拆解知识块并建立题型画像（考点×题型×分值×考频），存入复习数据库。用户上传资料并说明科目和考试日期后调用。',
        'description_en': 'Parse uploaded courseware PDF and past exam PDF, split into knowledge blocks and build exam profile, store into study database.',
        'semantic_apis': ["api_study"],
        'function': SkillFunction(
            id='a1000001-0000-4000-8000-000000000001',
            name='app.cosight.tool.study_toolkit.parse_study_material',
            description_zh='解析课件与往年卷，建立知识块库与题型画像',
            description_en='Parse courseware and past papers into knowledge blocks and exam profile',
            parameters={
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description_zh": "科目名称，如：离散数学",
                        "description_en": "Subject name"
                    },
                    "exam_date": {
                        "type": "string",
                        "description_zh": "考试日期，格式YYYY-MM-DD，如：2026-07-12",
                        "description_en": "Exam date in YYYY-MM-DD"
                    },
                    "courseware_path": {
                        "type": "string",
                        "description_zh": "课件PDF的文件路径或文件名",
                        "description_en": "Path or filename of courseware PDF"
                    },
                    "paper_path": {
                        "type": "string",
                        "description_zh": "往年卷/习题册PDF的路径或文件名，没有可不填",
                        "description_en": "Optional path of past exam PDF"
                    }
                },
                "required": ["subject", "exam_date", "courseware_path"]
            }
        )
    }


def generate_study_plan_skill():
    return {
        'skill_name': 'generate_study_plan',
        'skill_type': "function",
        'display_name_zh': '生成复习计划',
        'display_name_en': 'Generate Study Plan',
        'description_zh': '用确定性排期算法生成或重排多科统筹复习计划：拓扑排序保依赖、考前留仿真卷日、按紧迫度分配、含遗忘曲线。解析完资料后、或用户要求调整计划时（如每天可用时间变了）调用。若返回时间缺口警告，必须向用户说明并提供2-3套取舍方案由用户选择，不可擅自砍内容。',
        'description_en': 'Generate or re-plan the multi-subject study schedule with a deterministic algorithm.',
        'semantic_apis': ["api_study"],
        'function': SkillFunction(
            id='a1000001-0000-4000-8000-000000000002',
            name='app.cosight.tool.study_toolkit.generate_study_plan',
            description_zh='确定性排期算法生成/重排复习计划',
            description_en='Deterministic scheduling for study plan',
            parameters={
                "type": "object",
                "properties": {
                    "daily_minutes": {
                        "type": "integer",
                        "description_zh": "用户每天可用的复习分钟数，默认240（4小时）",
                        "description_en": "Available minutes per day, default 240"
                    },
                    "reason": {
                        "type": "string",
                        "description_zh": "本次生成/重排的原因，会记入重规划日志，如：首次生成/用户时间变更",
                        "description_en": "Reason for (re)planning, logged for audit"
                    },
                    "subject": {
                        "type": "string",
                        "description_zh": "要生成计划的科目；留空才表示统筹全部已建科目",
                        "description_en": "Optional subject; omit only for an all-subject plan"
                    }
                },
                "required": []
            }
        )
    }


def get_today_tasks_skill():
    return {
        'skill_name': 'get_today_tasks',
        'skill_type': "function",
        'display_name_zh': '获取今日任务卡',
        'display_name_en': 'Get Today Tasks',
        'description_zh': '获取今天的任务卡（含各科考试倒计时和任务清单）。用户每天开始学习、或问今天学什么时调用。拿到后应展示给用户并询问是否需要增改，确认后按顺序进入讲解。',
        'description_en': 'Get today task card with exam countdowns.',
        'semantic_apis': ["api_study"],
        'function': SkillFunction(
            id='a1000001-0000-4000-8000-000000000003',
            name='app.cosight.tool.study_toolkit.get_today_tasks',
            description_zh='读取今日任务卡与考试倒计时',
            description_en='Read today tasks and countdowns',
            parameters={"type": "object", "properties": {}, "required": []}
        )
    }


def record_quiz_score_skill():
    return {
        'skill_name': 'record_quiz_score',
        'skill_type': "function",
        'display_name_zh': '记录小测成绩',
        'display_name_en': 'Record Quiz Score',
        'description_zh': '记录某知识块随堂小测的得分率并更新掌握度（新掌握度=0.3×旧+0.7×得分率）。返回值会指示下一步：达标进入下一块 / 触发一轮补讲补测 / 入薄弱池并自动重排计划。每次小测批改完后必须调用。',
        'description_en': 'Record quiz score rate for a knowledge block, update mastery, and get next-step instruction.',
        'semantic_apis': ["api_study"],
        'function': SkillFunction(
            id='a1000001-0000-4000-8000-000000000004',
            name='app.cosight.tool.study_toolkit.record_quiz_score',
            description_zh='记录小测得分率并更新掌握度',
            description_en='Record quiz score and update mastery',
            parameters={
                "type": "object",
                "properties": {
                    "block_id": {
                        "type": "integer",
                        "description_zh": "知识块id（任务卡里有标注）",
                        "description_en": "Knowledge block id"
                    },
                    "score_rate": {
                        "type": "number",
                        "description_zh": "本次小测得分率，0~100",
                        "description_en": "Score rate 0-100"
                    }
                },
                "required": ["block_id", "score_rate"]
            }
        )
    }


def get_study_overview_skill():
    return {
        'skill_name': 'get_study_overview',
        'skill_type': "function",
        'display_name_zh': '查看学习总览',
        'display_name_en': 'Get Study Overview',
        'description_zh': '查看总体学习状况：各科知识块数、平均掌握度、考试倒计时、薄弱池、最近的重规划记录。用户问进度/学得怎么样时调用。',
        'description_en': 'Overview of study progress, mastery, weak pool and replan history.',
        'semantic_apis': ["api_study"],
        'function': SkillFunction(
            id='a1000001-0000-4000-8000-000000000005',
            name='app.cosight.tool.study_toolkit.get_study_overview',
            description_zh='读取学习总览',
            description_en='Read study overview',
            parameters={"type": "object", "properties": {}, "required": []}
        )
    }

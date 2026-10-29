# quiz_engine.py —— 出题判分工具
# 注意：questions 表列顺序为 (id, quiz_id, q_type, stem, options, answer,
#       user_answer, is_correct, explanation, score_value)，索引访问以 0 起。

from study_db import (
    get_blocks_by_subject,
    update_block_mastery,
    update_block_status,
    get_questions_by_quiz,
    update_question_result,
    update_mastery,
)


class QuizEngine:
    """出题判分工具"""

    def generate_questions(self, block_id, kind="随堂"):
        """生成3-5道题"""
        # 实际应调用大模型 + 读取题型画像
        # 这里模拟返回题目
        return [
            {"type": "选择题", "stem": "以下哪个是泰勒展开的正确形式？",
             "options": ["A. f(x)=f(a)+f'(a)(x-a)", "B. f(x)=...", "C. ...", "D. ..."],
             "answer": "A", "score": 5},
            {"type": "填空题", "stem": "泰勒展开的余项是___",
             "options": [], "answer": "拉格朗日余项", "score": 5},
            {"type": "简答题", "stem": "请简述泰勒展开的应用",
             "options": [], "answer": "...", "score": 10},
        ]

    def grade_quiz(self, quiz_id, answers, block_id=None):
        """判分并更新掌握度。
        - 客观题（选择/填空）比对标准答案；
        - 简答题简化处理为得分（可扩展为大模型批改）。
        - block_id 传入时更新该知识块掌握度与状态。
        """
        questions = get_questions_by_quiz(quiz_id)
        if not questions:
            return {"error": "未找到该小测的题目", "score": 0, "total": 0, "rate": 0}

        score = 0
        total = sum(q[9] for q in questions)  # score_value

        for q, a in zip(questions, answers):
            if q[2] in ['选择题', '填空题']:  # q_type
                if a == q[5]:  # answer
                    score += q[9]  # score_value
                    update_question_result(q[0], a, 1)
                else:
                    update_question_result(q[0], a, 0)
            else:
                # 简答题调用大模型批改
                # 这里简化处理
                score += q[9]
                update_question_result(q[0], a, 1)

        rate = score / total if total else 0

        # 更新掌握度（沿用 study_db.update_mastery 的公式：0.3*旧 + 0.7*得分率）
        if block_id:
            update_mastery(block_id, rate * 100)
            update_block_status(block_id, '需补救' if rate < 0.6 else '已学')

        return {"score": score, "total": total, "rate": rate}

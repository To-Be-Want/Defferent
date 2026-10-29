# mode_switch.py —— 模式切换（需新建）

from datetime import datetime
from study_db import get_subject

def get_current_mode(subject_id):
    """
    根据考试日期自动判断当前模式
    返回：{"mode": "日常学习" 或 "考前突击", "days_left": int}
    """
    subject = get_subject(subject_id)
    if not subject or not subject[2]:  # exam_date
        return {"mode": "日常学习", "days_left": None}
    
    exam_date = subject[2]
    exam_dt = datetime.strptime(exam_date, "%Y-%m-%d")
    days_left = (exam_dt - datetime.now()).days
    
    if 0 < days_left <= 21:
        return {"mode": "考前突击", "days_left": days_left}
    else:
        return {"mode": "日常学习", "days_left": days_left}


def switch_mode(subject_id, target_mode=None):
    """
    手动切换模式
    如果target_mode为None，则自动判断
    """
    mode_info = get_current_mode(subject_id)
    if target_mode and target_mode != mode_info['mode']:
        mode_info['mode'] = target_mode
    return mode_info
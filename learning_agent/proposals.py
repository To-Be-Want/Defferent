"""Preview exact scheduler rows; only server-verified user confirmation may commit."""
import hashlib
import json
import math
import secrets
from datetime import date, datetime
from zoneinfo import ZoneInfo

import scheduler
import study_db
from study_preferences import get_preferences


def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def fingerprint(conn):
    # Any changed learning input or plan invalidates an old preview.
    payload = {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
               for table in ('subjects', 'blocks', 'plans', 'exam_questions', 'exam_profile', 'past_papers')}
    payload['preferences'] = get_preferences()
    payload['personal_tasks'] = ([dict(row) for row in conn.execute('SELECT * FROM personal_tasks ORDER BY id')]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_tasks'").fetchone() else [])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preview_plan(daily_minutes=None, reason='用户请求', subject=''):
    minutes = get_preferences()['default_daily_minutes'] if daily_minutes is None else daily_minutes
    if type(minutes) is not int or not 15 <= minutes <= 1440:
        raise ValueError('每天可用时间须为15—1440分钟')
    sid = study_db.get_subject_id_by_name(subject) if subject and subject != '全部' else None
    if subject and subject != '全部' and not sid:
        raise ValueError('未找到该科目，请先上传并解析课件')
    with study_db.get_conn() as conn:
        before = fingerprint(conn)
        names = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM blocks')}
        subjects = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM subjects')}
        old_rows = [dict(r) for r in conn.execute('''SELECT p.* FROM plans p JOIN
            (SELECT subject_id,MAX(version) v FROM plans GROUP BY subject_id) l
            ON p.subject_id=l.subject_id AND p.version=l.v''') if sid is None or r['subject_id'] == sid]
    preview = scheduler.build_plan(daily_minutes=minutes, subject_id=sid, verbose=False, dry_run=True, today=today())
    with study_db.get_conn() as conn:
        if before != fingerprint(conn):
            raise ValueError('学习记录刚刚发生变化，请重新预览')
    old = {(r['subject_id'], r['block_id'], r['task_type']): r for r in old_rows}
    changes = []
    for row in preview['rows']:
        day, bid, kind, duration, subject_id, _, _ = row
        prior = old.pop((subject_id, bid, kind), None)
        if not prior or prior['date'] != day or prior['est_minutes'] != duration:
            changes.append({'block_id': bid, 'subject_id': subject_id, 'task': kind,
                            'old_date': prior['date'] if prior else None, 'new_date': day,
                            'delay_days': max(0, (date.fromisoformat(day)-date.fromisoformat(prior['date'])).days) if prior else 0,
                            'old_minutes': prior['est_minutes'] if prior else 0, 'new_minutes': duration})
    changes.extend({'block_id': r['block_id'], 'subject_id': r['subject_id'], 'task': r['task_type'],
                    'old_date': r['date'], 'new_date': None, 'old_minutes': r['est_minutes'],
                    'new_minutes': 0, 'warning': '原任务不在新方案中，确认前请检查'} for r in old.values())
    for change in changes:
        change['name'] = names.get(change['block_id'], '综合训练')
        change['subject'] = subjects.get(change['subject_id'], '')
    # Include unchanged subjects and personal items when reporting total daily load.
    loads = {}
    with study_db.get_conn() as conn:
        for row in conn.execute('''SELECT p.date,p.est_minutes FROM plans p JOIN
            (SELECT subject_id,MAX(version) v FROM plans GROUP BY subject_id) l
            ON p.subject_id=l.subject_id AND p.version=l.v
            WHERE (? IS NOT NULL AND p.subject_id!=?) AND p.status!='完成' ''', (sid, sid)):
            loads[row['date']] = loads.get(row['date'], 0)+row['est_minutes']
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_tasks'").fetchone():
            for row in conn.execute("SELECT date,est_minutes FROM personal_tasks WHERE status!='完成'"):
                loads[row['date']] = loads.get(row['date'], 0)+row['est_minutes']
    for row in preview['rows']:
        loads[row[0]] = loads.get(row[0], 0)+row[3]
    overages = [{'date': day, 'minutes': total, 'over_minutes': total-minutes}
                for day, total in sorted(loads.items()) if total > minutes]
    return {'kind': 'plan', 'code': secrets.token_hex(3), 'fingerprint': before,
            'created_date': today().isoformat(), 'reason': reason, 'subject': subject,
            'changes': changes, 'overages': overages, 'extra_minutes': sum(r[3] for r in preview['rows'])-sum(r['est_minutes'] for r in old_rows),
            **preview}


def describe(proposal):
    if proposal['kind'] == 'exam_date':
        body = f"修改《{proposal['subject']}》考试日期为：{proposal['date_text']}。考试倒计时将变化；不会自动重排现有计划。"
    else:
        lines = [f"复习计划预览：每天{proposal['daily_minutes']}分钟，排入总时长变化{proposal['extra_minutes']:+}分钟。"]
        for c in proposal['changes']:
            lines.append(f"《{c['subject']}》{c['name']}·{c['task']}："
                         f"{c['old_date'] or '新增'} → {c['new_date'] or '移出新计划'}，"
                         f"{c['old_minutes']} → {c['new_minutes']}分钟"
                         + (f"，推迟{c['delay_days']}天" if c.get('delay_days') else ''))
        for gap in proposal['gaps']:
            lines.append(f"《{gap['subject']}》考前排不下{gap['minutes']}分钟：{'、'.join(gap['unscheduled'])}。"
                         f"按当前每日时长至少还需{math.ceil(gap['minutes']/proposal['daily_minutes'])}个学习日，不能保证考前完成。")
        for day in proposal.get('overages', []):
            lines.append(f"{day['date']} 连同已有事项共{day['minutes']}分钟，超出每日可用时间{day['over_minutes']}分钟；需增加时间或另行调整，不能保证当天完成。")
        if proposal['past_hints']:
            lines.append('考试已过，未重排：'+'、'.join(proposal['past_hints']))
        lines.append('以上时间为排期估计；实际学习速度可能不同。')
        body = '\n'.join(lines)
    return body + f"\n\n尚未修改。回复“确认执行 {proposal['code']}”保存，或回复“取消”。"


def apply(proposal):
    with study_db.get_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS agent_applied(code TEXT PRIMARY KEY, result TEXT NOT NULL)')
        old = conn.execute('SELECT result FROM agent_applied WHERE code=?', (proposal['code'],)).fetchone()
        if old:
            return old['result']
        if proposal['created_date'] != today().isoformat() or fingerprint(conn) != proposal['fingerprint']:
            raise ValueError('方案已过期或学习记录有变化，请重新预览后确认')
        if proposal['kind'] == 'exam_date':
            # Resolve natural dates in preview; never let a confirmation invent a new date.
            conn.execute("UPDATE subjects SET exam_date=?,exam_status='pending' WHERE id=?",
                         (proposal['date_text'], proposal['subject_id']))
            result = '考试日期已按确认修改，现有复习计划未重排。'
        else:
            version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM plans').fetchone()[0]
            conn.executemany('''INSERT INTO plans(version,date,block_id,task_type,est_minutes,subject_id,resource,paper_ref)
                VALUES (?,?,?,?,?,?,?,?)''', [(version, *r) for r in proposal['rows']])
            conn.execute('INSERT INTO plan_events(time,trigger,description,version_before,version_after) VALUES (?,?,?,?,?)',
                         (datetime.now().isoformat(), '用户确认', proposal['reason'], proposal['base_version'], version))
            result = f'已保存你确认的复习计划 v{version}。'
        conn.execute('INSERT INTO agent_applied VALUES (?,?)', (proposal['code'], result))
    return result


def quiz_advice(block):
    minutes = int(block['est_minutes'] * .6) + scheduler.QUIZ_MINUTES
    return (f"建议为《{block['name']}》安排约{minutes}分钟回攻。若当天没有空余时间，后续任务可能顺延；"
            '具体推迟日期和考前时间缺口需重新预览计算。当前复习计划未改动。'
            '你可以回到聊天说“预览复习计划调整”，查看完整影响后再决定。')

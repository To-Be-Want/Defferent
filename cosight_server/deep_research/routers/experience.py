"""Account-scoped quiz actions and persistent engine run details."""
import json
import re
import time
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException
import study_db
from user_context import user_root

experienceRouter=APIRouter(prefix='/deep-research')


@experienceRouter.get('/runs')
def runs():
    folder=user_root()/'work_space'
    result=[]
    for path in sorted(folder.glob('deep-*/result.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
        try:
            record=json.loads(path.read_text(encoding='utf-8'));snapshot=record.get('snapshot')
            if not snapshot:continue
            if snapshot.get('run_status')=='running' and time.time()-snapshot.get('updated_at',0)>120:
                snapshot['run_status']='interrupted'
                snapshot['notice']='执行状态长时间未更新，可能服务已重启或连接中断，请重试。'
            result.append({'query':record.get('query',''),'snapshot':snapshot,'sources':record.get('sources',[])})
        except (ValueError,OSError):continue
    return {'runs':result}


@experienceRouter.get('/quizzes/{token}')
def quiz(token:str):
    with study_db.get_conn() as conn:
        row=conn.execute('SELECT * FROM quizzes WHERE token=?',(token,)).fetchone()
        if not row:raise HTTPException(404,'试卷不存在')
        questions=[dict(q) for q in conn.execute('SELECT id,q_type,stem,options,score_value,user_answer FROM questions WHERE quiz_id=? ORDER BY id',(row['id'],))]
        for q in questions:q['options']=json.loads(q['options'] or '[]')
        has_metadata=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quiz_metadata'").fetchone()
        meta=conn.execute('SELECT title,subject FROM quiz_metadata WHERE token=?',(token,)).fetchone() if has_metadata else None
        return {'token':token,'questions':questions,'submitted':bool(row['submitted_at']),
            'title':meta['title'] if meta else '随堂小测','subject':meta['subject'] if meta else ''}


@experienceRouter.post('/quizzes/{token}/read-answer')
def read_answer(token:str,body:dict=Body(...)):
    quiz(token)
    from app.cosight.tool.study_toolkit import _resolve
    from material_analyst import extract_pdf_text
    identifier=body.get('upload_id','')
    try:path=Path(_resolve(str(identifier)))
    except (ValueError,PermissionError):raise HTTPException(404,'图片不存在') from None
    if path.suffix.lower() not in ('.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'):raise HTTPException(400,'请上传解答图片')
    text=extract_pdf_text(str(path),max_chars=6000)
    return {'text':text,'requires_confirmation':True,'notice':'请核对识别文字，修正后再提交批改。'}


@experienceRouter.post('/quizzes/{token}/mistakes/{question_id}')
def save_quiz_mistake(token:str,question_id:int):
    with study_db.get_conn() as conn:
        quiz_row=conn.execute('SELECT * FROM quizzes WHERE token=?',(token,)).fetchone()
        if not quiz_row or not quiz_row['submitted_at']:raise HTTPException(409,'请先提交小测')
        q=conn.execute('SELECT * FROM questions WHERE quiz_id=? AND id=?',(quiz_row['id'],question_id)).fetchone()
        if not q:raise HTTPException(404,'题目不存在')
        columns={r[1] for r in conn.execute('PRAGMA table_info(mistakes)')}
        existing=conn.execute('SELECT id FROM mistakes WHERE question=? AND user_answer=?',(q['stem'],q['user_answer'] or '')).fetchone()
        if existing:return {'saved':True,'id':existing['id']}
        has_metadata=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quiz_metadata'").fetchone()
        meta=conn.execute('SELECT subject FROM quiz_metadata WHERE token=?',(token,)).fetchone() if has_metadata else None
    identifier=study_db.save_mistake(quiz_row['block_id'],q['stem'],q['user_answer'] or '',q['answer'],q['explanation'] or '',meta['subject'] if meta else '')
    return {'saved':True,'id':identifier}

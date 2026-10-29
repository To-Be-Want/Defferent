"""Account-scoped, offline mistake notebook. No model calls are required."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool
import study_db

mistakeRouter = APIRouter(prefix="/deep-research/mistakes")
SUBJECT_SQL = "COALESCE(NULLIF(TRIM(subject),''),'未分类')"


class NewMistake(BaseModel):
    subject: str = Field(default="", max_length=40)
    question: str = Field(min_length=1, max_length=16000)
    correct_answer: str = Field(min_length=1, max_length=16000)
    explanation: str = Field(default="", max_length=20000)


class Review(BaseModel):
    is_correct: StrictBool


class Mastery(BaseModel):
    mastered: StrictBool


@mistakeRouter.get("")
def list_mistakes(subject: str = Query("", max_length=40), q: str = Query("", max_length=200),
                  status: Literal["all", "pending", "mastered"] = "all",
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    clauses, values = [], []
    if subject:
        clauses.append(SUBJECT_SQL + "=?")
        values.append(subject)
    if q.strip():
        clauses.append("(instr(lower(question),lower(?))>0 OR instr(lower(COALESCE(knowledge_points,'')),lower(?))>0)")
        values.extend([q.strip(), q.strip()])
    if status != "all":
        clauses.append("mastered=?")
        values.append(1 if status == "mastered" else 0)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with study_db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM mistakes" + where, values).fetchone()[0]
        summary = conn.execute("SELECT COUNT(*) total,COALESCE(SUM(CASE WHEN mastered=1 THEN 1 ELSE 0 END),0) mastered FROM mistakes").fetchone()
        groups = conn.execute("SELECT " + SUBJECT_SQL + " name,COUNT(*) count FROM mistakes GROUP BY " + SUBJECT_SQL + " ORDER BY name").fetchall()
        rows = conn.execute("SELECT * FROM mistakes" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                            [*values, page_size, (page-1)*page_size]).fetchall()
    return {"items": [dict(row) for row in rows], "total": count, "page": page, "page_size": page_size,
            "subjects": [dict(row) for row in groups], "summary": {
                "total": summary["total"], "mastered": summary["mastered"],
                "pending": summary["total"] - summary["mastered"]}}


@mistakeRouter.post("")
def add_mistake(body: NewMistake):
    if not body.question.strip() or not body.correct_answer.strip():
        raise HTTPException(400, "题目与参考答案不能为空")
    mistake_id = study_db.save_mistake(None, body.question.strip(), "", body.correct_answer.strip(),
                                      body.explanation.strip(), body.subject.strip())
    return {"id": mistake_id, "success": True}


@mistakeRouter.patch("/{mistake_id}")
def set_mastery(mistake_id: int, body: Mastery):
    with study_db.get_conn() as conn:
        cursor = conn.execute("UPDATE mistakes SET mastered=?,consecutive_correct=? WHERE id=?",
                             (int(body.mastered), 0, mistake_id))
        if not cursor.rowcount:
            raise HTTPException(404, "错题不存在")
    return {"success": True, "mastered": body.mastered}


@mistakeRouter.post("/{mistake_id}/reviews")
def record_review(mistake_id: int, body: Review):
    try:
        result = study_db.record_mistake_review(mistake_id, body.is_correct)
    except ValueError:
        raise HTTPException(404, "错题不存在") from None
    return {"success": True, **result, "grading": "self_assessed"}

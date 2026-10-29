"""Account-scoped retrieval, with visible source names and character locations."""
import json
import re
from pathlib import Path

import material_analyst as ma
import study_db
from app.cosight.tool.study_toolkit import _resolve
from cosight_server.deep_research.services.study_library import list_materials, material_path


def course_context(query, files=(), subject_id=None, cached_only=False, block_id=None):
    subject = ''
    if subject_id:
        with study_db.get_conn() as conn:
            row = conn.execute('SELECT name FROM subjects WHERE id=?', (subject_id,)).fetchone()
            subject = row['name'] if row else ''
    entries = list_materials(subject)
    from . import source_index, material_cache
    file_paths = {_resolve(str(path)) for path in files}
    if file_paths:
        entries = [item for item in entries if str(material_path(item['upload_id']).resolve()) in file_paths]
    allowed = [item['upload_id'] for item in entries]
    indexed = source_index.lookup(query, subject_id, block_id, allowed)
    if indexed['excerpts']:
        return json.dumps(indexed, ensure_ascii=False)
    # Old parsed files can acquire links locally from a cache, once per cache revision.
    for item in entries[:12]:
        path = material_path(item['upload_id'])
        target = material_cache.location(path) if path and path.is_file() else None
        if not target or not target.is_file():
            continue
        with study_db.get_conn() as conn:
            source_index.schema(conn)
            row = conn.execute('SELECT MAX(created_at) FROM material_text_versions WHERE upload_id=? AND file_signature=? AND active=1',
                               (item['upload_id'],target.stem)).fetchone()
        if row[0] is None or row[0] < target.stat().st_mtime:
            source_index.index_cached(item['upload_id'],path)
    indexed = source_index.lookup(query, subject_id, block_id, allowed)
    if indexed['excerpts']:
        return json.dumps(indexed, ensure_ascii=False)
    names = {str(material_path(item['upload_id'])): item['filename'] for item in entries}
    selected = [(_resolve(str(p)), names.get(_resolve(str(p)), Path(p).name)) for p in files]
    for item in entries:
        path = str(material_path(item['upload_id']))
        if path not in [p for p, _ in selected]:
            selected.append((path, item['filename']))
    terms = set(re.findall(r'[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}', query))
    terms |= {query[i:i+2] for i in range(len(query)-1) if all('\u4e00' <= c <= '\u9fff' for c in query[i:i+2])}
    chunks, errors, pending = [], [], []
    for path, name in selected[:12]:
        try:
            from . import material_cache
            safe_path = _resolve(path)
            text = material_cache.read(safe_path)
            if text is None:
                cheap = Path(path).suffix.lower() in ('.txt', '.md', '.json') and Path(path).stat().st_size <= 2_000_000
                if cached_only and not cheap:
                    pending.append({'source': name, 'status': '尚无文本缓存；确需原文时调用read_course_material读取，不能宣称已读'})
                    continue
                text = ma.extract_pdf_text(safe_path, max_chars=120000)
                material_cache.write(safe_path, text)
            for offset in range(0, len(text), 1800):
                chunk = text[offset:offset+2200]
                score = sum(chunk.lower().count(t.lower()) for t in terms)
                chunks.append((score, {'source': name, 'start_char': offset, 'text': chunk}))
        except Exception:
            errors.append({'source': name, 'status': '读取失败，不能据此宣称已核对'})
    chunks.sort(key=lambda x: x[0], reverse=True)
    # An explicit first read also persists locations for subsequent teaching turns.
    if not cached_only:
        for item in entries[:12]:
            path = material_path(item['upload_id'])
            if path and path.is_file():
                source_index.index_cached(item['upload_id'],path)
        indexed = source_index.lookup(query, subject_id, block_id, allowed)
        if indexed['excerpts']:
            return json.dumps(indexed, ensure_ascii=False)
    return json.dumps({'excerpts': [value for _, value in chunks[:8]], 'errors': errors, 'pending': pending,
                       'limits': '本次最多检索12份文件、每份前120000字符；摘录不是全文。'}, ensure_ascii=False)

"""Versioned original excerpts and many-to-many knowledge/source locations."""
import hashlib
import math
import json
import re
import time
from pathlib import Path

import study_db
from . import material_cache
from .progress import report

FORMAT = 'source-locations-v1'


def schema(conn):
    # Use individual statements: executescript would commit a caller's transaction.
    conn.execute('''CREATE TABLE IF NOT EXISTS material_text_versions (
        version TEXT PRIMARY KEY, upload_id TEXT NOT NULL, file_signature TEXT NOT NULL,
        cache_key TEXT NOT NULL, indexed_chars INTEGER NOT NULL, truncated INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS material_version_upload ON material_text_versions(upload_id,active)')
    conn.execute('''CREATE TABLE IF NOT EXISTS material_text_chunks (
        chunk_id TEXT PRIMARY KEY, version TEXT NOT NULL REFERENCES material_text_versions(version),
        sequence INTEGER NOT NULL, page_number INTEGER, source_unit TEXT NOT NULL,
        paragraph_number INTEGER NOT NULL, start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
        content_type TEXT NOT NULL, text TEXT NOT NULL, region_json TEXT)''')
    conn.execute('CREATE INDEX IF NOT EXISTS material_chunks_version ON material_text_chunks(version,sequence)')
    conn.execute('''CREATE TABLE IF NOT EXISTS knowledge_source_links (
        block_id INTEGER NOT NULL REFERENCES blocks(id), chunk_id TEXT NOT NULL REFERENCES material_text_chunks(chunk_id),
        sequence INTEGER NOT NULL, match_method TEXT NOT NULL,
        PRIMARY KEY(block_id,chunk_id))''')


def normalize(text):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text.lower())


def content_type(text):
    if re.search(r'```|\b(def |class |for\s*\(|while\s*\(|import |return |int main)|#include', text):
        return '代码'
    if re.search(r'例题|例\s*\d|示例', text):
        return '例题'
    if re.search(r'证明|推导', text):
        return '推导'
    if re.search(r'习题|练习|思考题', text):
        return '习题'
    return '概念'


def chunks(text, suffix):
    """Keep paragraphs and code fences intact. No invented pages for legacy text."""
    marker = re.compile(r'^\[(?:第\s*(\d+)\s*页|幻灯片\s*(\d+))\]\s*\n', re.M)
    markers = list(marker.finditer(text))
    sections = []
    if markers:
        if markers[0].start():
            sections.append((0, markers[0].start(), None, 'text'))
        for i, match in enumerate(markers):
            sections.append((match.end(), markers[i+1].start() if i+1<len(markers) else len(text),
                             int(match[1] or match[2]), 'page' if match[1] else 'slide'))
    else:
        sections = [(0, len(text), 1 if suffix in ('.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff') else None,
                     'image' if suffix in ('.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff') else 'text')]
    sequence = 0
    for start, end, page, unit in sections:
        section = text[start:end]
        # Paragraph breaks inside a fenced code block are not splitting boundaries.
        boundaries = [start]
        fenced = False
        for line in re.finditer(r'.*(?:\n|$)', section):
            value = line[0]
            if value.lstrip().startswith(('```', '~~~')):
                fenced = not fenced
            if not fenced and not value.strip() and line.end() > boundaries[-1]-start:
                boundaries.append(start+line.end())
        boundaries.append(end)
        paragraph = 0
        for a, b in zip(boundaries, boundaries[1:]):
            raw = text[a:b]
            if not raw.strip() or '未识别本页' in raw:
                continue
            paragraph += 1
            left = a+len(raw)-len(raw.lstrip())
            right = b-(len(raw)-len(raw.rstrip()))
            yield {'sequence': sequence, 'page_number': page, 'source_unit': unit,
                   'paragraph_number': paragraph, 'start_char': left, 'end_char': right,
                   'content_type': content_type(text[left:right]), 'text': text[left:right]}
            sequence += 1


def link_blocks(conn, upload_id, version, blocks):
    """Source IDs from the parser or conservative lexical matches, with provenance."""
    rows = [dict(r) for r in conn.execute('SELECT * FROM material_text_chunks WHERE version=? ORDER BY sequence', (version,))]
    by_id = {r['chunk_id']: r for r in rows}
    for block in blocks:
        # Replace this source's links only; preserve other files for a shared block.
        conn.execute('''DELETE FROM knowledge_source_links WHERE block_id=? AND chunk_id IN
            (SELECT c.chunk_id FROM material_text_chunks c JOIN material_text_versions v ON v.version=c.version WHERE v.upload_id=?)''',
                     (block['id'], upload_id))
        selected, method = [], 'model_reference'
        for identifier in block.get('source_chunk_ids', []):
            if isinstance(identifier, str) and identifier in by_id and identifier not in selected:
                selected.append(identifier)
        if not selected:
            name, chapter = normalize(block['name']), normalize(block.get('chapter') or '')
            if len(name) >= 2:
                selected = [r['chunk_id'] for r in rows if name in normalize(r['text'])][:6]
            method = 'exact_name'
            if not selected and len(chapter) >= 3:
                selected = [r['chunk_id'] for r in rows if chapter in normalize(r['text'])][:3]
                method = 'chapter_match'
            if not selected and len(name) >= 4:
                # Legacy block names often combine several headings. Weight rare
                # character pairs, and record this as a tentative lexical match.
                grams = {name[i:i+2] for i in range(len(name)-1)}
                normalized_rows = [(r,normalize(r['text'])) for r in rows]
                weights = {g:1+math.log((len(rows)+1)/(1+sum(g in t for _,t in normalized_rows))) for g in grams}
                total = sum(weights.values()) or 1
                scored = []
                for row,text in normalized_rows:
                    shared = {g for g in grams if g in text}
                    score = sum(weights[g] for g in shared)/total
                    if len(shared)>=3 and score>=.35:
                        scored.append((score,row['chunk_id']))
                scored.sort(reverse=True)
                selected = [identifier for score,identifier in scored[:3]
                            if score >= scored[0][0]*.8] if scored else []
                method = 'keyword_match'
        # Adjacent paragraphs/pages carry the continuation of examples and derivations.
        chosen = {by_id[i]['sequence'] for i in selected[:8]}
        neighbors = {n+1 for n in chosen}
        for row in rows:
            if row['sequence'] in chosen or (chosen and row['sequence'] in neighbors):
                conn.execute('INSERT OR REPLACE INTO knowledge_source_links VALUES (?,?,?,?)',
                             (block['id'], row['chunk_id'], row['sequence'], method if row['sequence'] in chosen else 'continuation'))


def index_cached(upload_id, path, conn=None, blocks=None):
    """Local-only indexing; absence of cache never triggers OCR or a model request."""
    doc = material_cache.document(path)
    signature = material_cache.signature(path)
    if not doc or not signature:
        return None
    text = doc.get('text', '')
    version = hashlib.sha256((FORMAT+upload_id+signature+text).encode()).hexdigest()
    own = conn is None
    conn = conn or study_db.get_conn()
    try:
        schema(conn)
        exists = conn.execute('SELECT 1 FROM material_text_versions WHERE version=?', (version,)).fetchone()
        if not exists:
            report('indexing')
            conn.execute('UPDATE material_text_versions SET active=0 WHERE upload_id=?', (upload_id,))
            conn.execute('INSERT INTO material_text_versions VALUES (?,?,?,?,?,?,1,?)',
                         (version, upload_id, signature, signature+'.json', len(text), int(doc.get('truncated',False)), time.time()))
            for row in chunks(text, Path(path).suffix.lower()):
                identifier = hashlib.sha256((version+':'+str(row['sequence'])).encode()).hexdigest()[:24]
                conn.execute('INSERT INTO material_text_chunks VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',
                             (identifier,version,row['sequence'],row['page_number'],row['source_unit'],row['paragraph_number'],
                              row['start_char'],row['end_char'],row['content_type'],row['text']))
        if blocks is None:
            has_parse = conn.execute("SELECT 1 FROM sqlite_master WHERE name='material_parses'").fetchone()
            parsed = conn.execute('SELECT block_ids FROM material_parses WHERE upload_id=?',(upload_id,)).fetchone() if has_parse else None
            identifiers = json.loads(parsed['block_ids']) if parsed else []
            blocks = [dict(r) for bid in identifiers for r in conn.execute('SELECT id,name,chapter FROM blocks WHERE id=?',(bid,))]
        if blocks:
            missing = [block for block in blocks if not conn.execute('''SELECT 1 FROM knowledge_source_links l
                JOIN material_text_chunks c ON c.chunk_id=l.chunk_id WHERE c.version=? AND l.block_id=? LIMIT 1''',
                (version,block['id'])).fetchone()]
            if missing:
                link_blocks(conn, upload_id, version, missing)
        if own:
            conn.commit()
        return version
    finally:
        if own:
            conn.close()


def lookup(query='', subject_id=None, block_id=None, upload_ids=None):
    """Retrieve already-associated excerpts via indexed keys, never scan PDF/cache."""
    from cosight_server.deep_research.services.study_library import material_path, list_materials
    report('locating')
    with study_db.get_conn() as conn:
        schema(conn)
        if block_id is not None:
            blocks = [dict(r) for r in conn.execute("SELECT * FROM blocks WHERE id=? AND status NOT IN ('已归档','跳过')",(block_id,))]
        else:
            blocks = [dict(r) for r in conn.execute("SELECT * FROM blocks WHERE status NOT IN ('已归档','跳过') AND (? IS NULL OR subject_id=?)",(subject_id,subject_id))]
            normalized = normalize(query)
            topic = normalize(re.sub(r'请|给我|帮我|讲一讲|讲讲|讲解|解释|介绍|学习|一下|是什么|怎么理解', '',query))
            blocks = [b for b in blocks if (len(normalize(b['name']))>=2 and normalize(b['name']) in normalized)
                      or (len(topic)>=3 and topic in normalize(b['name']))][:3]
        excerpts, invalid = [], []
        names = {m['upload_id']:m['filename'] for m in list_materials()}
        seen = set()
        for block in blocks:
            for row in conn.execute('''SELECT c.*,v.upload_id,v.file_signature,v.cache_key,v.truncated,l.match_method
                FROM knowledge_source_links l JOIN material_text_chunks c ON c.chunk_id=l.chunk_id
                JOIN material_text_versions v ON v.version=c.version
                WHERE l.block_id=? AND v.active=1 ORDER BY l.sequence''',(block['id'],)):
                if upload_ids is not None and row['upload_id'] not in upload_ids:
                    continue
                path = material_path(row['upload_id'])
                try:
                    valid = bool(path and path.is_file() and material_cache.signature(path) == row['file_signature'])
                except OSError:
                    valid = False
                if not valid:
                    invalid.append(row['upload_id'])
                    continue
                if row['chunk_id'] in seen:
                    continue
                seen.add(row['chunk_id'])
                url = '/api/nae-deep-research/v1/deep-research/materials/'+row['upload_id']+'/source'
                excerpts.append({'block_id': block['id'], 'knowledge_point': block['name'], 'source': names.get(row['upload_id'],''),
                    'upload_id':row['upload_id'],'version':row['version'],'chunk_id':row['chunk_id'],
                    'page_number':row['page_number'],'source_unit':row['source_unit'], 'paragraph_number':row['paragraph_number'],
                    'start_char':row['start_char'],'end_char':row['end_char'],'cache_key':row['cache_key'],
                    'content_type':row['content_type'],'match_method':row['match_method'],
                    'source_url':url+('#page='+str(row['page_number']) if row['source_unit']=='page' else ''),
                    'text':row['text'], 'partial_document':bool(row['truncated'])})
        # Preserve full examples/code blocks; do not silently cut a fragment in half.
        selected, omitted, budget = [], [], 16000
        for excerpt in excerpts:
            if len(selected) >= 10 or len(excerpt['text']) > budget:
                omitted.append(excerpt['chunk_id'])
                continue
            selected.append(excerpt)
            budget -= len(excerpt['text'])
        report('reading', count=len(selected))
        return {'excerpts':selected, 'omitted_chunks':omitted, 'retrieval':'knowledge_source_links', 'stale_uploads':sorted(set(invalid)),
                'limits':'关联位置来自原文标记或文本匹配；旧缓存无页码时不猜测。片段不等同完整文件。'}


def stats():
    """Small database-only summary for the material library; no PDF/cache reads."""
    with study_db.get_conn() as conn:
        schema(conn)
        rows = conn.execute('''SELECT v.upload_id,COUNT(DISTINCT c.chunk_id) chunks,
            COUNT(DISTINCT c.page_number) pages,COUNT(DISTINCT CASE WHEN b.status NOT IN ('跳过','已归档') THEN l.block_id END) linked_blocks,
            SUM(CASE WHEN l.match_method IN ('chapter_match','keyword_match') THEN 1 ELSE 0 END) tentative_links
            FROM material_text_versions v LEFT JOIN material_text_chunks c ON c.version=v.version
            LEFT JOIN knowledge_source_links l ON l.chunk_id=c.chunk_id LEFT JOIN blocks b ON b.id=l.block_id
            WHERE v.active=1 GROUP BY v.upload_id''').fetchall()
        return {row['upload_id']:dict(row) for row in rows}

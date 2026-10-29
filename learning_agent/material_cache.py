"""Reusable account-local extracted text, invalidated when the uploaded file changes."""
import hashlib
import json
import uuid
from pathlib import Path
from user_context import current_user, user_root


def location(path):
    if not current_user.get():
        return None
    path = Path(path).resolve()
    if not path.is_relative_to((user_root()/'upload_files').resolve()):
        return None
    stat = path.stat()
    key = hashlib.sha256(f'{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
    folder = user_root()/'material_text_cache'
    folder.mkdir(exist_ok=True)
    return folder/(key+'.json')


def read(path):
    try:
        target = location(path)
        return json.loads(target.read_text(encoding='utf-8'))['text'] if target else None
    except (OSError, ValueError, KeyError):
        return None


def document(path):
    try:
        target = location(path)
        return json.loads(target.read_text(encoding='utf-8')) if target else None
    except (OSError, ValueError):
        return None


def signature(path):
    target = location(path)
    return target.stem if target else None


def write(path, text):
    try:
        target = location(path)
        if not target:
            return
        previous = read(path)
        if previous and len(previous) >= len(text):
            return
        temp = target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        text_limit = 2_000_000
        temp.write_text(json.dumps({'text': text[:text_limit], 'truncated': len(text) > text_limit,
                                    'file_signature': target.stem}, ensure_ascii=False), encoding='utf-8')
        temp.replace(target)
    except OSError:
        pass  # A cache failure must never make parsing or a reply fail.

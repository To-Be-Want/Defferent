"""Backfill source links from existing per-account caches, without OCR or model calls."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from user_context import data_root,current_user
from cosight_server.deep_research.services.study_library import list_materials,material_path
from learning_agent.source_index import index_cached


def main():
    indexed = pending = 0
    for folder in (data_root()/'users').glob('*'):
        if not folder.is_dir() or len(folder.name)!=32:
            continue
        token=current_user.set({'id':folder.name})
        try:
            for item in list_materials():
                path=material_path(item['upload_id'])
                if path and path.is_file() and index_cached(item['upload_id'],path):
                    indexed+=1
                else:
                    pending+=1
        finally:
            current_user.reset(token)
    print(f'Cached source indexes: {indexed}; missing caches deferred: {pending}. No model/OCR calls.')


if __name__=='__main__':
    main()

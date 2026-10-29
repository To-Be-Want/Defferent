"""Deploy an explicit Git revision, with private backups and health-check rollback.

Run on the Ubuntu server as member1. Credentials remain outside the shared code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.request


def run(*args,**kwargs):return subprocess.run(args,check=True,**kwargs)


def allowed(relative):
    parts=Path(relative).parts
    if not parts or Path(relative).is_absolute() or '..' in parts:return False
    if relative.startswith('cosight_server/web/data/'):return True
    if any(p in {'data','upload_files','uploads','work_space','logs','.venv','.git','__pycache__'} for p in parts):return False
    return Path(relative).name not in {'.env','mcp_server_config.json'} and not relative.endswith(('.db','.pem','.key'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repository',type=Path,default=Path.home()/'.local/share/xingguizhixue/repository')
    parser.add_argument('--project',type=Path,default=Path('/home/ubuntu/Co-Sight'))
    parser.add_argument('--revision',required=True)
    parser.add_argument('--baseline',type=Path,help='Optional source manifest for first deployment drift detection')
    args=parser.parse_args()
    project=args.project.resolve();repository=args.repository.resolve()
    if str(project)!='/home/ubuntu/Co-Sight':raise SystemExit('Unexpected deployment target')
    for record in (project/'data/users').glob('*/work_space/deep-*/result.json'):
        try:snapshot=json.loads(record.read_text()).get('snapshot',{})
        except (ValueError,OSError):continue
        if snapshot.get('run_status')=='running' and time.time()-snapshot.get('updated_at',0)<120:
            raise SystemExit('An engine task is still running; retry deployment after it finishes.')
    if not re.fullmatch('[a-f0-9]{40}',args.revision):raise SystemExit('Use an explicit full commit SHA')
    run('git','-C',str(repository),'fetch','origin','main',timeout=60)
    run('git','-C',str(repository),'cat-file','-e',args.revision+'^{commit}')
    paths=subprocess.check_output(['git','-C',str(repository),'ls-tree','-r','-z','--name-only',args.revision]).decode('utf-8').rstrip('\0').split('\0')
    paths=[name for name in paths if allowed(name)]
    state=Path.home()/'.local/share/xingguizhixue';state.mkdir(parents=True,exist_ok=True);state.chmod(0o700)
    if args.baseline:
        manifest=json.loads(args.baseline.read_text())
        expected={v['path']:v['sha256'] for v in manifest['files']}
        for relative in paths:
            path=project/relative
            if relative in expected and path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=expected[relative]:
                raise SystemExit('Server source changed since download: '+relative)
    backup=state/'backups'/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True,exist_ok=False);backup.chmod(0o700)
    records={}
    # Save exactly the files this release can replace; never overwrite config/data.
    for relative in paths:
        source=project/relative
        if source.is_symlink():raise SystemExit('Refuse to replace a symlink: '+relative)
        if not source.resolve().is_relative_to(project):raise SystemExit('Unsafe destination')
        records[relative]=source.exists()
        if source.is_file():
            destination=backup/'source'/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    (backup/'files.json').write_text(json.dumps(records))
    run('sudo','-n','/usr/bin/systemctl','stop','cosight')
    try:
        with tarfile.open(backup/'runtime-data.tar.gz','w:gz') as archive:
            for name in ('data','upload_files','study_library','work_space','study.db'):
                path=project/name
                if path.exists():archive.add(path,arcname=name)
        for db in (project/'data').rglob('*.db'):
            target=backup/'sqlite'/db.relative_to(project);target.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(str(db)) as source,sqlite3.connect(str(target)) as dest:source.backup(dest)
        for relative in paths:
            content=subprocess.check_output(['git','-C',str(repository),'show',args.revision+':'+relative])
            destination=project/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(content)
        run(str(project/'.venv/bin/python'),'-m','compileall','-q',str(project/'cloud_app.py'),str(project/'cosight_server/deep_research'))
        run('sudo','-n','/usr/bin/systemctl','start','cosight')
        for attempt in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:7788/healthz',timeout=2) as response:
                    if response.status==200 and json.load(response).get('mode')=='multi-user-study':break
                time.sleep(1)
            except Exception:time.sleep(1)
        else:raise RuntimeError('Service health check failed')
        (state/'deployed.json').write_text(json.dumps({'revision':args.revision,'backup':str(backup),'deployed_at':time.time()}))
        print(json.dumps({'deployed':args.revision,'backup':str(backup),'health':'ok'}))
    except Exception:
        run('sudo','-n','/usr/bin/systemctl','stop','cosight')
        for relative,existed in records.items():
            destination=project/relative
            if existed:shutil.copy2(backup/'source'/relative,destination)
            else:destination.unlink(missing_ok=True)
        run('sudo','-n','/usr/bin/systemctl','start','cosight')
        raise


if __name__=='__main__':main()

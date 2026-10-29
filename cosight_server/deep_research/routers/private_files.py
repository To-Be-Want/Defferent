from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from user_context import contained_path, user_root

privateFileRouter = APIRouter()


def private_file(path):
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@privateFileRouter.get("/deep-research/materials/{upload_id}/download")
def download_material(upload_id: str):
    from cosight_server.deep_research.services.study_library import material_path
    path = material_path(upload_id)
    if not path:
        raise HTTPException(404, "资料不存在")
    root = user_root() / "upload_files"
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(404, "资料不存在") from None
    return private_file(resolved)


@privateFileRouter.get('/deep-research/materials/{upload_id}/source')
def view_material_source(upload_id: str):
    from cosight_server.deep_research.services.study_library import material_path
    path = material_path(upload_id)
    if not path:
        raise HTTPException(404, '资料不存在')
    try:
        resolved = contained_path(user_root()/'upload_files', path.resolve())
    except (ValueError, PermissionError):
        raise HTTPException(404, '资料不存在') from None
    if not resolved.is_file():
        raise HTTPException(404, '资料不存在')
    types = {'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg',
             '.webp':'image/webp','.txt':'text/plain','.md':'text/plain','.json':'text/plain'}
    return FileResponse(resolved, media_type=types.get(resolved.suffix.lower(), 'application/octet-stream'),
                        filename=resolved.name, content_disposition_type='inline',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@privateFileRouter.get("/upload_files/{relative:path}")
def download_upload(relative: str):
    try:
        path = contained_path(user_root() / "upload_files", relative)
    except PermissionError:
        raise HTTPException(404, "文件不存在") from None
    return private_file(path)


@privateFileRouter.get("/work_space/{relative:path}")
def download_output(relative: str):
    try:
        path = contained_path(user_root() / "work_space", relative)
    except PermissionError:
        raise HTTPException(404, "文件不存在") from None
    return private_file(path)

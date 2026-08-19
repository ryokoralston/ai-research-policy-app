from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from models.user import User
from schemas.workspace import WorkspaceFileContent, WorkspaceFileInfo
from services.auth import get_current_user
from services.text_editor_tool import resolve_workspace_path, user_workspace_dir

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("", response_model=list[WorkspaceFileInfo])
def list_workspace_files(current_user: User = Depends(get_current_user)):
    """Return all files in the CALLER'S draft workspace (recursive), sorted by
    relative path. Each user has their own workspace root, so this never lists
    another user's drafts."""
    root = Path(user_workspace_dir(current_user.id))
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            stat = p.stat()
            files.append(WorkspaceFileInfo(
                name=p.relative_to(root).as_posix(),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime),
            ))
    return files


@router.get("/file", response_model=WorkspaceFileContent)
def get_workspace_file(
    name: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    """Return the text content of one file in the caller's own workspace. 404 if
    missing, 400 if the name escapes that user's workspace root (see
    services.text_editor_tool.resolve_workspace_path — the containment check is
    what stops ../ and symlink escapes into another user's directory)."""
    try:
        path = resolve_workspace_path(name, user_workspace_dir(current_user.id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    content = path.read_text(encoding="utf-8", errors="replace")
    return WorkspaceFileContent(name=name, content=content)

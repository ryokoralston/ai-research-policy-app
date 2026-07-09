from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from schemas.workspace import WorkspaceFileContent, WorkspaceFileInfo
from services.text_editor_tool import WORKSPACE_DIR, resolve_workspace_path

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("", response_model=list[WorkspaceFileInfo])
def list_workspace_files():
    """Return all files in the draft workspace (recursive), sorted by relative path."""
    root = Path(WORKSPACE_DIR)
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
def get_workspace_file(name: str = Query(...)):
    """Return the text content of one workspace file. 404 if missing, 400 if the
    name escapes the workspace root (see services.text_editor_tool.resolve_workspace_path)."""
    try:
        path = resolve_workspace_path(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    content = path.read_text(encoding="utf-8", errors="replace")
    return WorkspaceFileContent(name=name, content=content)

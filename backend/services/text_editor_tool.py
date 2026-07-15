"""Anthropic-defined text editor tool (text_editor_20250728) executor.

This is a SCHEMA-LESS server tool: Claude already knows the full input
schema for str_replace_based_edit_tool, so TEXT_EDITOR_TOOL below carries no
input_schema — just {"type": ..., "name": ...}. Claude then emits ordinary
tool_use blocks named str_replace_based_edit_tool with input.command in
{view, create, str_replace, insert} (undo_edit is NOT supported on the
20250728 version and is rejected below).

Lets Claude maintain a small "draft workspace" of files (memos, briefs,
notes) across a chat turn, the same way search_documents and the reminder
tools give it document search and reminder-setting abilities — wired into
the same manual tool loop in anthropic_client.stream_chat_with_tools.

Security: `path`/`name` values arriving here are untrusted model output.
Every command resolves its path through resolve_workspace_path(), which
confines the result inside WORKSPACE_DIR (see docstring below) before any
file is opened.

Read-before-write guard: the `create` command enforces that an existing file
may only be overwritten if it was `view`ed earlier in the same chat turn.
"""
import os
from pathlib import Path

TEXT_EDITOR_TOOL = {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"}
TEXT_EDITOR_TOOL_NAME = "str_replace_based_edit_tool"

# Derived from __file__ so it resolves correctly regardless of cwd.
# Created lazily (see _ensure_dir) — never at import time.
WORKSPACE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workspace")
)

# Guardrail: refuse to write files beyond this size (bytes).
MAX_FILE_BYTES = 262144


def _ensure_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_workspace_path(name: str, workspace_dir: str | None = None) -> Path:
    """Resolve an untrusted path/name against the workspace root, confining it inside.

    Both absolute-looking paths ("/notes.md", "/etc/passwd") and relative ones
    ("notes.md", "sub/notes.md") are treated as workspace-relative: leading
    "/" and "./" segments are stripped before the name is joined onto the
    workspace root. The joined path is then resolved (following any
    symlinks) and verified to still be inside the resolved workspace root —
    this is what actually rejects ".." escapes and symlink escapes, not the
    string stripping.

    Raises ValueError with a descriptive message if the resolved path would
    land outside the workspace root. Never opens/writes the raw value.
    """
    root = _ensure_dir(Path(workspace_dir) if workspace_dir else Path(WORKSPACE_DIR))
    root_resolved = root.resolve()

    raw = (name or "").strip()
    while raw.startswith("/") or raw.startswith("./"):
        raw = raw[1:] if raw.startswith("/") else raw[2:]

    candidate = (root_resolved / raw).resolve() if raw else root_resolved
    if not candidate.is_relative_to(root_resolved):
        raise ValueError("path escapes the workspace")
    return candidate


def _rel(path: Path, root: Path) -> str:
    resolved_root = root.resolve()
    try:
        rel = path.relative_to(resolved_root).as_posix()
    except ValueError:
        rel = path.name
    return rel or "."


def _numbered_lines(text: str, view_range: list | None) -> str:
    lines = text.split("\n")
    start, end = 1, len(lines)
    if view_range:
        if not (isinstance(view_range, (list, tuple)) and len(view_range) == 2):
            raise ValueError("view_range must be a [start, end] pair")
        start, end = view_range
        start = int(start)
        end = len(lines) if int(end) == -1 else int(end)
        if start < 1 or start > len(lines):
            raise ValueError(f"view_range start {start} is out of bounds (file has {len(lines)} lines)")
        if end < start:
            raise ValueError(f"view_range end {end} is before start {start}")
        end = min(end, len(lines))
    selected = lines[start - 1:end]
    return "\n".join(f"{i + start:6d}\t{line}" for i, line in enumerate(selected))


def _check_size(content: str) -> str | None:
    """Returns an error string if content exceeds the size cap, else None."""
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return "Error: file too large"
    return None


# ── Command handlers ────────────────────────────────────────────────────────

def _view(tool_input: dict, root: Path, *, viewed_paths: set[str] | None = None) -> str:
    raw_path = tool_input.get("path")
    if not raw_path:
        return "Error: 'path' is required."
    try:
        target = resolve_workspace_path(raw_path, str(root))
    except ValueError:
        return "Error: path escapes the workspace"

    if target.is_dir():
        entries = sorted(target.iterdir(), key=lambda p: p.name)
        lines = [f"{_rel(e, root)}/" if e.is_dir() else _rel(e, root) for e in entries]
        header = _rel(target, root)
        body = "\n".join(lines) if lines else "(empty directory)"
        return f"Directory listing for '{header}':\n{body}"

    if not target.is_file():
        return f"Error: file not found: '{_rel(target, root)}'"

    text = target.read_text(encoding="utf-8", errors="replace")
    try:
        numbered = _numbered_lines(text, tool_input.get("view_range"))
    except ValueError as exc:
        return f"Error: {exc}"

    # Record the viewed file path if a viewed_paths set was provided
    if viewed_paths is not None:
        viewed_paths.add(str(target))

    return f"{_rel(target, root)}:\n{numbered}"


def _create(tool_input: dict, root: Path, *, viewed_paths: set[str] | None = None) -> str:
    raw_path = tool_input.get("path")
    if not raw_path:
        return "Error: 'path' is required."
    if "file_text" not in tool_input:
        return "Error: 'file_text' is required."
    file_text = tool_input.get("file_text") or ""

    size_error = _check_size(file_text)
    if size_error:
        return size_error

    try:
        target = resolve_workspace_path(raw_path, str(root))
    except ValueError:
        return "Error: path escapes the workspace"

    # Read-before-write guard: if target exists and was not viewed, reject
    if target.is_file() and str(target) not in (viewed_paths or set()):
        existing_text = target.read_text(encoding="utf-8", errors="replace")
        line_count = existing_text.count("\n") + 1 if existing_text else 0
        return f"Error: file '{_rel(target, root)}' already exists ({line_count} lines). View it first to confirm you want to overwrite it, or use str_replace to edit it in place."

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(file_text, encoding="utf-8")
    line_count = file_text.count("\n") + 1 if file_text else 0
    return f"Created workspace file '{_rel(target, root)}' ({line_count} lines)."


def _str_replace(tool_input: dict, root: Path) -> str:
    raw_path = tool_input.get("path")
    if not raw_path:
        return "Error: 'path' is required."
    old_str = tool_input.get("old_str")
    if not old_str:
        return "Error: 'old_str' is required."
    new_str = tool_input.get("new_str", "") or ""

    try:
        target = resolve_workspace_path(raw_path, str(root))
    except ValueError:
        return "Error: path escapes the workspace"

    if not target.is_file():
        return f"Error: file not found: '{_rel(target, root)}'"

    text = target.read_text(encoding="utf-8", errors="replace")
    occurrences = text.count(old_str)
    if occurrences == 0:
        return "Error: old_str not found in file"
    if occurrences > 1:
        return (
            f"Error: old_str matches {occurrences} locations; "
            "provide more surrounding context to make it unique"
        )

    new_text = text.replace(old_str, new_str, 1)
    size_error = _check_size(new_text)
    if size_error:
        return size_error

    target.write_text(new_text, encoding="utf-8")
    return f"Replaced text in '{_rel(target, root)}'."


def _insert(tool_input: dict, root: Path) -> str:
    raw_path = tool_input.get("path")
    if not raw_path:
        return "Error: 'path' is required."
    if "insert_line" not in tool_input:
        return "Error: 'insert_line' is required."
    # The wire format calls this "insert_text"; accept "new_str" too since some
    # callers reuse the str_replace field name for the text being inserted.
    insert_text = tool_input.get("insert_text")
    if insert_text is None:
        insert_text = tool_input.get("new_str")
    if insert_text is None:
        return "Error: 'insert_text' is required."

    try:
        insert_line = int(tool_input.get("insert_line"))
    except (TypeError, ValueError):
        return f"Error: 'insert_line' must be an integer. Got: {tool_input.get('insert_line')!r}"

    try:
        target = resolve_workspace_path(raw_path, str(root))
    except ValueError:
        return "Error: path escapes the workspace"

    if not target.is_file():
        return f"Error: file not found: '{_rel(target, root)}'"

    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    if insert_line < 0 or insert_line > len(lines):
        return f"Error: insert_line {insert_line} is out of bounds (file has {len(lines)} lines)"

    insertion = insert_text.split("\n")
    new_lines = lines[:insert_line] + insertion + lines[insert_line:]
    new_text = "\n".join(new_lines)

    size_error = _check_size(new_text)
    if size_error:
        return size_error

    target.write_text(new_text, encoding="utf-8")
    return f"Inserted text into '{_rel(target, root)}' after line {insert_line}."


_HANDLERS = {
    "view": _view,
    "create": _create,
    "str_replace": _str_replace,
    "insert": _insert,
}


async def execute_text_editor_tool(tool_input: dict, workspace_dir: str | None = None, *, viewed_paths: set[str] | None = None) -> str:
    """Execute one str_replace_based_edit_tool call and return the result string.

    workspace_dir overrides WORKSPACE_DIR (used by tests to run against a
    tempfile.mkdtemp() sandbox instead of the real backend/workspace/ dir).

    viewed_paths (keyword-only, optional): a set to track which file paths have been
    viewed during this chat turn. When provided to view commands, the path is added
    to the set on success (file views only, not directory listings or errors). When
    provided to create commands, it enforces read-before-write: an existing file can
    only be overwritten if its path is already in the set. When None (the default),
    the guard applies (no files can be overwritten) but no tracking occurs — this
    preserves backward compatibility for standalone callers that don't pass viewed_paths.

    Async to match the tool_executor(name, input) -> str contract expected by
    anthropic_client.stream_chat_with_tools, even though every handler here is
    plain synchronous file I/O (same pattern as reminder_tools._sync_handler).
    """
    command = tool_input.get("command")
    if command == "undo_edit" or command not in _HANDLERS:
        return f"Error: unsupported command {command!r}"

    root = _ensure_dir(Path(workspace_dir) if workspace_dir else Path(WORKSPACE_DIR))
    handler = _HANDLERS[command]
    try:
        # Special-case view and create to pass viewed_paths; other handlers ignore it
        if command == "view":
            return _view(tool_input, root, viewed_paths=viewed_paths)
        elif command == "create":
            return _create(tool_input, root, viewed_paths=viewed_paths)
        else:
            return handler(tool_input, root)
    except Exception as exc:  # genuinely unexpected internal failure
        return f"Error: unexpected failure executing '{command}': {exc}"

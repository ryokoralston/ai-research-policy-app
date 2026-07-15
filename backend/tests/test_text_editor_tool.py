"""Unit tests for the text editor tool executor (services/text_editor_tool.py).

No Claude API / network calls — all tests exercise the Python functions directly
against a tempfile.mkdtemp() sandbox (never the real backend/workspace/ dir).
Run from the backend directory:
    ./venv/bin/python -m tests.test_text_editor_tool

Uses a plain assert-based runner, matching tests/test_reminder_tools.py.
"""
import asyncio
import os
import shutil
import sys
import tempfile

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from services.text_editor_tool import execute_text_editor_tool, resolve_workspace_path


# ── Test runner helpers ───────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name: str, fn):
    tmp = tempfile.mkdtemp()
    try:
        fn(tmp)
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _exec(tool_input: dict, workspace: str) -> str:
    return asyncio.run(execute_text_editor_tool(tool_input, workspace_dir=workspace))


# ── create + view roundtrip ────────────────────────────────────────────────────

def test_create_then_view_roundtrip(ws):
    result = _exec({"command": "create", "path": "notes.md", "file_text": "line one\nline two\nline three"}, ws)
    assert "Created workspace file 'notes.md'" in result, repr(result)
    assert "3 lines" in result, repr(result)

    viewed = _exec({"command": "view", "path": "notes.md"}, ws)
    assert viewed.startswith("notes.md:\n"), repr(viewed)
    assert "     1\tline one" in viewed, repr(viewed)
    assert "     2\tline two" in viewed, repr(viewed)
    assert "     3\tline three" in viewed, repr(viewed)


def test_create_overwrite_allowed(ws):
    """Overwrite is allowed after viewing with the same viewed_paths set."""
    viewed = set()
    # Create initial file
    asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "a.txt", "file_text": "first"},
        workspace_dir=ws
    ))
    # View it (adds to viewed)
    view_result = asyncio.run(execute_text_editor_tool(
        {"command": "view", "path": "a.txt"},
        workspace_dir=ws,
        viewed_paths=viewed
    ))
    assert "a.txt:" in view_result, repr(view_result)
    # Now overwrite (should succeed)
    result = asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "a.txt", "file_text": "second"},
        workspace_dir=ws,
        viewed_paths=viewed
    ))
    assert "Created workspace file 'a.txt'" in result, repr(result)
    # Verify content
    final_view = asyncio.run(execute_text_editor_tool(
        {"command": "view", "path": "a.txt"},
        workspace_dir=ws,
    ))
    assert "second" in final_view and "first" not in final_view, repr(final_view)


# ── view_range ─────────────────────────────────────────────────────────────────

def test_view_range_subset(ws):
    _exec({"command": "create", "path": "r.txt", "file_text": "a\nb\nc\nd\ne"}, ws)
    viewed = _exec({"command": "view", "path": "r.txt", "view_range": [2, 4]}, ws)
    assert "     2\tb" in viewed
    assert "     4\td" in viewed
    assert "\ta" not in viewed  # line 1 excluded
    assert "\te" not in viewed  # line 5 excluded


def test_view_range_end_minus_one_means_eof(ws):
    _exec({"command": "create", "path": "r2.txt", "file_text": "a\nb\nc"}, ws)
    viewed = _exec({"command": "view", "path": "r2.txt", "view_range": [2, -1]}, ws)
    assert "     2\tb" in viewed
    assert "     3\tc" in viewed


# ── directory listing ────────────────────────────────────────────────────────

def test_directory_listing(ws):
    _exec({"command": "create", "path": "top.txt", "file_text": "x"}, ws)
    _exec({"command": "create", "path": "sub/inner.txt", "file_text": "y"}, ws)
    viewed = _exec({"command": "view", "path": "."}, ws)
    assert "top.txt" in viewed, repr(viewed)
    assert "sub/" in viewed, repr(viewed)


# ── str_replace ──────────────────────────────────────────────────────────────

def test_str_replace_success(ws):
    _exec({"command": "create", "path": "s.txt", "file_text": "hello world"}, ws)
    result = _exec({"command": "str_replace", "path": "s.txt", "old_str": "world", "new_str": "there"}, ws)
    assert "Replaced text" in result, repr(result)
    viewed = _exec({"command": "view", "path": "s.txt"}, ws)
    assert "hello there" in viewed, repr(viewed)


def test_str_replace_zero_matches(ws):
    _exec({"command": "create", "path": "s2.txt", "file_text": "hello world"}, ws)
    result = _exec({"command": "str_replace", "path": "s2.txt", "old_str": "goodbye", "new_str": "x"}, ws)
    assert result == "Error: old_str not found in file", repr(result)


def test_str_replace_multiple_matches(ws):
    _exec({"command": "create", "path": "s3.txt", "file_text": "cat cat cat"}, ws)
    result = _exec({"command": "str_replace", "path": "s3.txt", "old_str": "cat", "new_str": "dog"}, ws)
    assert result.startswith("Error: old_str matches 3 locations"), repr(result)


# ── insert ───────────────────────────────────────────────────────────────────

def test_insert_at_beginning(ws):
    _exec({"command": "create", "path": "i.txt", "file_text": "first\nsecond"}, ws)
    result = _exec({"command": "insert", "path": "i.txt", "insert_line": 0, "insert_text": "zeroth"}, ws)
    assert "Inserted text" in result, repr(result)
    viewed = _exec({"command": "view", "path": "i.txt"}, ws)
    assert "     1\tzeroth" in viewed
    assert "     2\tfirst" in viewed
    assert "     3\tsecond" in viewed


def test_insert_mid_file(ws):
    _exec({"command": "create", "path": "i2.txt", "file_text": "a\nb\nc"}, ws)
    _exec({"command": "insert", "path": "i2.txt", "insert_line": 2, "insert_text": "b.5"}, ws)
    viewed = _exec({"command": "view", "path": "i2.txt"}, ws)
    assert "     1\ta" in viewed
    assert "     2\tb" in viewed
    assert "     3\tb.5" in viewed
    assert "     4\tc" in viewed


# ── path traversal ───────────────────────────────────────────────────────────

def test_traversal_dotdot_rejected(ws):
    result = _exec({"command": "view", "path": "../x"}, ws)
    assert result == "Error: path escapes the workspace", repr(result)


def test_traversal_nested_dotdot_rejected(ws):
    result = _exec({"command": "view", "path": "a/../../x"}, ws)
    assert result == "Error: path escapes the workspace", repr(result)


def test_absolute_looking_path_treated_as_workspace_relative(ws):
    """"/etc/passwd" is absolute-looking, but resolve_workspace_path strips the
    leading "/" and joins onto the workspace root — it never touches the real
    /etc/passwd, and stays confined inside the workspace (here it's simply a
    file that doesn't exist yet under <workspace>/etc/passwd)."""
    resolved = resolve_workspace_path("/etc/passwd", ws)
    assert str(resolved).startswith(os.path.realpath(ws)), repr(resolved)
    result = _exec({"command": "view", "path": "/etc/passwd"}, ws)
    assert "Error: file not found" in result, repr(result)


# ── resolve_workspace_path direct tests ─────────────────────────────────────

def test_resolve_workspace_path_raises_on_escape(ws):
    try:
        resolve_workspace_path("../../etc/passwd", ws)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_resolve_workspace_path_allows_nested_relative(ws):
    resolved = resolve_workspace_path("a/b/c.txt", ws)
    assert str(resolved).startswith(os.path.realpath(ws)), repr(resolved)


# ── unknown command / undo_edit ─────────────────────────────────────────────

def test_unknown_command_error(ws):
    result = _exec({"command": "delete_everything", "path": "x.txt"}, ws)
    assert result == "Error: unsupported command 'delete_everything'", repr(result)


def test_undo_edit_not_supported(ws):
    result = _exec({"command": "undo_edit", "path": "x.txt"}, ws)
    assert result == "Error: unsupported command 'undo_edit'", repr(result)


# ── file size cap ────────────────────────────────────────────────────────────

def test_create_file_too_large_rejected(ws):
    huge = "x" * (262144 + 1)
    result = _exec({"command": "create", "path": "huge.txt", "file_text": huge}, ws)
    assert result == "Error: file too large", repr(result)
    # Nothing should have been written
    result2 = _exec({"command": "view", "path": "huge.txt"}, ws)
    assert "Error: file not found" in result2, repr(result2)


# ── read-before-write guard (viewed_paths) ───────────────────────────────────

def test_create_new_file_with_viewed_paths_succeeds(ws):
    """(a) create on a NEW file with viewed_paths=set() → succeeds."""
    result = asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "new.md", "file_text": "content"},
        workspace_dir=ws,
        viewed_paths=set()
    ))
    assert "Created workspace file 'new.md'" in result, repr(result)


def test_create_existing_file_with_empty_viewed_paths_errors(ws):
    """(b) create on an EXISTING file with viewed_paths=set() → error with 'View it first'."""
    # Create initial file
    asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "existing.md", "file_text": "original\nlines"},
        workspace_dir=ws
    ))
    # Try to overwrite with empty viewed set
    result = asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "existing.md", "file_text": "new content"},
        workspace_dir=ws,
        viewed_paths=set()
    ))
    assert result.startswith("Error:"), repr(result)
    assert "View it first" in result, repr(result)
    assert "2 lines" in result, repr(result)  # counts existing file's lines


def test_view_then_create_succeeds(ws):
    """(c) view the file (passing the same set), then create → succeeds (overwrites)."""
    viewed = set()
    # Create initial file
    asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "draft.md", "file_text": "original"},
        workspace_dir=ws
    ))
    # View it (adds to viewed)
    view_result = asyncio.run(execute_text_editor_tool(
        {"command": "view", "path": "draft.md"},
        workspace_dir=ws,
        viewed_paths=viewed
    ))
    assert "draft.md:" in view_result, repr(view_result)
    assert len(viewed) == 1, f"viewed set should have 1 entry, got {viewed}"
    # Now create/overwrite (should succeed)
    create_result = asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "draft.md", "file_text": "updated content"},
        workspace_dir=ws,
        viewed_paths=viewed
    ))
    assert "Created workspace file 'draft.md'" in create_result, repr(create_result)
    # Verify content changed
    view_after = asyncio.run(execute_text_editor_tool(
        {"command": "view", "path": "draft.md"},
        workspace_dir=ws,
    ))
    assert "updated content" in view_after, repr(view_after)
    assert "original" not in view_after, repr(view_after)


def test_fresh_empty_set_still_blocks_overwrite(ws):
    """(d) a FRESH empty set (simulating a new turn) → create on existing errors again."""
    # Create initial file
    asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "file.md", "file_text": "first"},
        workspace_dir=ws
    ))
    # Try with a fresh empty set (new turn)
    result = asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "file.md", "file_text": "second"},
        workspace_dir=ws,
        viewed_paths=set()  # Fresh empty set
    ))
    assert result.startswith("Error:"), repr(result)
    assert "View it first" in result, repr(result)


def test_viewed_paths_none_blocks_overwrite_and_view_works(ws):
    """(e) viewed_paths=None → create on existing errors, view doesn't crash."""
    # Create initial file
    asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "test.md", "file_text": "content"},
        workspace_dir=ws
    ))
    # View with None (no tracking)
    view_result = asyncio.run(execute_text_editor_tool(
        {"command": "view", "path": "test.md"},
        workspace_dir=ws,
        viewed_paths=None
    ))
    assert "test.md:" in view_result, repr(view_result)
    # Try to create/overwrite with None (should error)
    create_result = asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "test.md", "file_text": "new"},
        workspace_dir=ws,
        viewed_paths=None
    ))
    assert create_result.startswith("Error:"), repr(create_result)
    assert "View it first" in create_result, repr(create_result)


def test_directory_view_not_added_to_viewed_paths(ws):
    """(f) directory view does NOT add to viewed_paths; failed view does NOT add."""
    viewed = set()
    # Create a file and then view the directory
    asyncio.run(execute_text_editor_tool(
        {"command": "create", "path": "file.md", "file_text": "content"},
        workspace_dir=ws
    ))
    dir_result = asyncio.run(execute_text_editor_tool(
        {"command": "view", "path": "."},
        workspace_dir=ws,
        viewed_paths=viewed
    ))
    assert "Directory listing" in dir_result, repr(dir_result)
    assert len(viewed) == 0, f"viewing directory should not add to viewed_paths, got {viewed}"
    # View a missing file (error case)
    missing_result = asyncio.run(execute_text_editor_tool(
        {"command": "view", "path": "missing.md"},
        workspace_dir=ws,
        viewed_paths=viewed
    ))
    assert "Error: file not found" in missing_result, repr(missing_result)
    assert len(viewed) == 0, f"failed file view should not add to viewed_paths, got {viewed}"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nRunning text editor tool tests...\n")

    _run("create + view: roundtrip with numbered lines", test_create_then_view_roundtrip)
    _run("create: overwrite allowed", test_create_overwrite_allowed)
    _run("view: view_range subset", test_view_range_subset)
    _run("view: view_range end=-1 means EOF", test_view_range_end_minus_one_means_eof)
    _run("view: directory listing", test_directory_listing)
    _run("str_replace: success", test_str_replace_success)
    _run("str_replace: 0 matches -> error", test_str_replace_zero_matches)
    _run("str_replace: multiple matches -> error", test_str_replace_multiple_matches)
    _run("insert: at beginning (line 0)", test_insert_at_beginning)
    _run("insert: mid-file", test_insert_mid_file)
    _run("path traversal: '../x' rejected", test_traversal_dotdot_rejected)
    _run("path traversal: 'a/../../x' rejected", test_traversal_nested_dotdot_rejected)
    _run("path: absolute-looking '/etc/passwd' treated as workspace-relative", test_absolute_looking_path_treated_as_workspace_relative)
    _run("resolve_workspace_path: raises on escape", test_resolve_workspace_path_raises_on_escape)
    _run("resolve_workspace_path: allows nested relative path", test_resolve_workspace_path_allows_nested_relative)
    _run("unknown command -> error", test_unknown_command_error)
    _run("undo_edit -> unsupported error", test_undo_edit_not_supported)
    _run("create: file too large rejected", test_create_file_too_large_rejected)
    _run("read-before-write: (a) create new file with viewed_paths=set()", test_create_new_file_with_viewed_paths_succeeds)
    _run("read-before-write: (b) create existing with viewed_paths=set() errors", test_create_existing_file_with_empty_viewed_paths_errors)
    _run("read-before-write: (c) view then create succeeds", test_view_then_create_succeeds)
    _run("read-before-write: (d) fresh empty set blocks overwrite", test_fresh_empty_set_still_blocks_overwrite)
    _run("read-before-write: (e) viewed_paths=None blocks & view works", test_viewed_paths_none_blocks_overwrite_and_view_works)
    _run("read-before-write: (f) directory & failed view not tracked", test_directory_view_not_added_to_viewed_paths)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")

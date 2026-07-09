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
    _exec({"command": "create", "path": "a.txt", "file_text": "first"}, ws)
    result = _exec({"command": "create", "path": "a.txt", "file_text": "second"}, ws)
    assert "Created workspace file 'a.txt'" in result, repr(result)
    viewed = _exec({"command": "view", "path": "a.txt"}, ws)
    assert "second" in viewed and "first" not in viewed, repr(viewed)


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

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")

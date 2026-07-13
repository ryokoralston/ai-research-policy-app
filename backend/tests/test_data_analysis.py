"""Unit tests for the Data Lab service (services/data_analysis.py): the pure
`_content_type_for` extension mapping and the content-list extractors
(`_extract_code_blocks`, `_extract_stdout_results`, `_extract_output_file_ids`)
against fake, dict-shaped content lists (no live API calls, no real DB).

Run from the backend directory:
    ./venv/bin/python -m tests.test_data_analysis
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.data_analysis as data_analysis


# ── _content_type_for ─────────────────────────────────────────────────────────

def test_content_type_for_csv():
    assert data_analysis._content_type_for("data.csv") == "text/csv"


def test_content_type_for_json():
    assert data_analysis._content_type_for("data.json") == "application/json"


def test_content_type_for_xlsx():
    assert data_analysis._content_type_for("data.xlsx") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_content_type_for_txt():
    assert data_analysis._content_type_for("notes.txt") == "text/plain"


def test_content_type_for_case_insensitive():
    assert data_analysis._content_type_for("DATA.CSV") == "text/csv"


def test_content_type_for_unsupported_returns_none():
    assert data_analysis._content_type_for("archive.zip") is None


def test_content_type_for_no_extension_returns_none():
    assert data_analysis._content_type_for("README") is None


# ── _extract_code_blocks ───────────────────────────────────────────────────────

def test_extract_code_blocks_empty_content():
    assert data_analysis._extract_code_blocks([]) == []


def test_extract_code_blocks_none_content():
    assert data_analysis._extract_code_blocks(None) == []


def test_extract_code_blocks_single_bash_command():
    content = [
        {"type": "text", "text": "Let me look at the data."},
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "bash_code_execution",
            "input": {"command": "import pandas as pd\ndf = pd.read_csv('data.csv')\nprint(df.head())"},
        },
    ]
    codes = data_analysis._extract_code_blocks(content)
    assert codes == ["import pandas as pd\ndf = pd.read_csv('data.csv')\nprint(df.head())"], codes


def test_extract_code_blocks_multiple_runs_counted():
    content = [
        {"type": "server_tool_use", "name": "bash_code_execution", "input": {"command": "print(1)"}},
        {"type": "text", "text": "interleaved text"},
        {"type": "server_tool_use", "name": "bash_code_execution", "input": {"command": "print(2)"}},
    ]
    codes = data_analysis._extract_code_blocks(content)
    assert codes == ["print(1)", "print(2)"], codes


def test_extract_code_blocks_falls_back_to_code_key():
    content = [{"type": "server_tool_use", "name": "code_execution", "input": {"code": "x = 1"}}]
    assert data_analysis._extract_code_blocks(content) == ["x = 1"]


def test_extract_code_blocks_ignores_non_server_tool_use_blocks():
    content = [
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "name": "some_client_tool", "input": {"command": "should not appear"}},
    ]
    assert data_analysis._extract_code_blocks(content) == []


def test_extract_code_blocks_stringifies_missing_code_key():
    content = [{"type": "server_tool_use", "name": "bash_code_execution", "input": {"restart": True}}]
    codes = data_analysis._extract_code_blocks(content)
    assert len(codes) == 1
    assert "restart" in codes[0]


# ── _extract_stdout_results ────────────────────────────────────────────────────

def test_extract_stdout_results_empty_content():
    assert data_analysis._extract_stdout_results([]) == []


def test_extract_stdout_results_success():
    content = [{
        "type": "bash_code_execution_tool_result",
        "tool_use_id": "srvtoolu_1",
        "content": {
            "type": "bash_code_execution_result",
            "stdout": "hello world\n",
            "stderr": "",
            "return_code": 0,
            "content": [],
        },
    }]
    results = data_analysis._extract_stdout_results(content)
    assert results == [{"text": "hello world\n", "return_code": 0}], results


def test_extract_stdout_results_trims_long_stdout():
    long_stdout = "x" * 5000
    content = [{
        "type": "bash_code_execution_tool_result",
        "content": {"type": "bash_code_execution_result", "stdout": long_stdout, "stderr": "", "return_code": 0, "content": []},
    }]
    results = data_analysis._extract_stdout_results(content)
    assert len(results) == 1
    assert len(results[0]["text"]) <= data_analysis.STDOUT_TRIM_CHARS + len("\n… (truncated)")
    assert results[0]["text"].endswith("(truncated)")


def test_extract_stdout_results_error_result_surfaces_error_code():
    content = [{
        "type": "bash_code_execution_tool_result",
        "content": {"type": "bash_code_execution_tool_result_error", "error_code": "unavailable"},
    }]
    results = data_analysis._extract_stdout_results(content)
    assert len(results) == 1
    assert "unavailable" in results[0]["text"]
    assert results[0]["return_code"] is None


def test_extract_stdout_results_multiple_runs():
    content = [
        {"type": "bash_code_execution_tool_result", "content": {"type": "bash_code_execution_result", "stdout": "a", "stderr": "", "return_code": 0, "content": []}},
        {"type": "bash_code_execution_tool_result", "content": {"type": "bash_code_execution_result", "stdout": "b", "stderr": "", "return_code": 1, "content": []}},
    ]
    results = data_analysis._extract_stdout_results(content)
    assert [r["text"] for r in results] == ["a", "b"]
    assert [r["return_code"] for r in results] == [0, 1]


# ── _extract_output_file_ids ──────────────────────────────────────────────────

def test_extract_output_file_ids_empty_content():
    assert data_analysis._extract_output_file_ids([]) == []


def test_extract_output_file_ids_no_generated_files():
    content = [{
        "type": "bash_code_execution_tool_result",
        "content": {"type": "bash_code_execution_result", "stdout": "ok", "stderr": "", "return_code": 0, "content": []},
    }]
    assert data_analysis._extract_output_file_ids(content) == []


def test_extract_output_file_ids_single_generated_file():
    content = [{
        "type": "bash_code_execution_tool_result",
        "content": {
            "type": "bash_code_execution_result",
            "stdout": "", "stderr": "", "return_code": 0,
            "content": [{"type": "bash_code_execution_output", "file_id": "file_abc123"}],
        },
    }]
    assert data_analysis._extract_output_file_ids(content) == ["file_abc123"]


def test_extract_output_file_ids_multiple_files_across_blocks():
    content = [
        {
            "type": "bash_code_execution_tool_result",
            "content": {
                "type": "bash_code_execution_result", "stdout": "", "stderr": "", "return_code": 0,
                "content": [{"type": "bash_code_execution_output", "file_id": "file_1"}],
            },
        },
        {"type": "text", "text": "here is the chart"},
        {
            "type": "bash_code_execution_tool_result",
            "content": {
                "type": "bash_code_execution_result", "stdout": "", "stderr": "", "return_code": 0,
                "content": [
                    {"type": "bash_code_execution_output", "file_id": "file_2"},
                    {"type": "bash_code_execution_output", "file_id": "file_3"},
                ],
            },
        },
    ]
    assert data_analysis._extract_output_file_ids(content) == ["file_1", "file_2", "file_3"]


def test_extract_output_file_ids_ignores_error_results():
    content = [{
        "type": "bash_code_execution_tool_result",
        "content": {"type": "bash_code_execution_tool_result_error", "error_code": "unavailable"},
    }]
    assert data_analysis._extract_output_file_ids(content) == []


# ── Test runner ────────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning data_analysis tests...\n")

    _run("_content_type_for: csv", test_content_type_for_csv)
    _run("_content_type_for: json", test_content_type_for_json)
    _run("_content_type_for: xlsx", test_content_type_for_xlsx)
    _run("_content_type_for: txt", test_content_type_for_txt)
    _run("_content_type_for: case insensitive", test_content_type_for_case_insensitive)
    _run("_content_type_for: unsupported returns None", test_content_type_for_unsupported_returns_none)
    _run("_content_type_for: no extension returns None", test_content_type_for_no_extension_returns_none)

    _run("_extract_code_blocks: empty content", test_extract_code_blocks_empty_content)
    _run("_extract_code_blocks: None content", test_extract_code_blocks_none_content)
    _run("_extract_code_blocks: single bash command", test_extract_code_blocks_single_bash_command)
    _run("_extract_code_blocks: multiple runs counted", test_extract_code_blocks_multiple_runs_counted)
    _run("_extract_code_blocks: falls back to code key", test_extract_code_blocks_falls_back_to_code_key)
    _run("_extract_code_blocks: ignores non-server_tool_use blocks", test_extract_code_blocks_ignores_non_server_tool_use_blocks)
    _run("_extract_code_blocks: stringifies missing code key", test_extract_code_blocks_stringifies_missing_code_key)

    _run("_extract_stdout_results: empty content", test_extract_stdout_results_empty_content)
    _run("_extract_stdout_results: success", test_extract_stdout_results_success)
    _run("_extract_stdout_results: trims long stdout", test_extract_stdout_results_trims_long_stdout)
    _run("_extract_stdout_results: error result surfaces error_code", test_extract_stdout_results_error_result_surfaces_error_code)
    _run("_extract_stdout_results: multiple runs", test_extract_stdout_results_multiple_runs)

    _run("_extract_output_file_ids: empty content", test_extract_output_file_ids_empty_content)
    _run("_extract_output_file_ids: no generated files", test_extract_output_file_ids_no_generated_files)
    _run("_extract_output_file_ids: single generated file", test_extract_output_file_ids_single_generated_file)
    _run("_extract_output_file_ids: multiple files across blocks", test_extract_output_file_ids_multiple_files_across_blocks)
    _run("_extract_output_file_ids: ignores error results", test_extract_output_file_ids_ignores_error_results)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")

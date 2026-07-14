"""Tests for services/mcp_bridge.py — merging MCP-server tools (registered
in the repo-root .mcp.json) into rag_service.answer_question's tool loop.

Unit-level sections (config parsing, prefixing/exclusion, cache-retry
semantics, call_mcp_tool's result handling, rag_service wiring) fake out
MCPClient / load_server_params / stream_chat_with_tools entirely — no
subprocess, no live API. The final section is a real end-to-end test, in
the same style as test_mcp_client.py: it spawns the actual mcp_server.py as
a subprocess and talks to it through the real mcp_bridge functions.

Run from the backend directory:
    ./venv/bin/python -m tests.test_mcp_bridge
"""
import asyncio
import json
import os
import sys
import tempfile
import types as _types_module

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

# rag_service imports chromadb / sentence_transformers transitively (via
# rag.vector_store / services.embedding_service) — stub them out so the
# wiring section below never needs those packages installed, same as
# test_rag_answer.py / test_query_router.py.
for _name in ("chromadb", "sentence_transformers"):
    if _name not in sys.modules:
        sys.modules[_name] = _types_module.ModuleType(_name)

import mcp.types as types
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import services.mcp_bridge as mcp_bridge
import rag.retriever as retriever_module
import services.rag_service as rag_service

_SERVER_SCRIPT = os.path.join(_BACKEND_DIR, "mcp_server.py")

# The real MCP_CONFIG_PATH mcp_bridge computed at import time (repo-root
# .mcp.json) — every test below points it somewhere else and restores this.
_REAL_CONFIG_PATH = mcp_bridge.MCP_CONFIG_PATH


def _set_config(config: dict | None) -> str:
    """Write `config` to a fresh temp file and point mcp_bridge.MCP_CONFIG_PATH
    at it; config=None instead leaves the path pointing at a file that does
    not exist (the "missing file" case). Returns the path (test's job to
    clean up via _restore_config). Also resets the schema cache so each test
    starts from a clean slate.
    """
    fd, path = tempfile.mkstemp(suffix=".mcp_bridge_test.json")
    os.close(fd)
    if config is None:
        os.remove(path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f)
    mcp_bridge.MCP_CONFIG_PATH = path
    mcp_bridge.reset_cache()
    return path


def _restore_config(path: str) -> None:
    mcp_bridge.MCP_CONFIG_PATH = _REAL_CONFIG_PATH
    mcp_bridge.reset_cache()
    if os.path.exists(path):
        os.remove(path)


# ── load_server_params ───────────────────────────────────────────────────

def test_load_server_params_missing_file_returns_empty():
    path = _set_config(None)
    try:
        assert mcp_bridge.load_server_params() == {}
    finally:
        _restore_config(path)


def test_load_server_params_malformed_json_returns_empty():
    fd, path = tempfile.mkstemp(suffix=".mcp_bridge_test.json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ this is not valid json")
    mcp_bridge.MCP_CONFIG_PATH = path
    mcp_bridge.reset_cache()
    try:
        assert mcp_bridge.load_server_params() == {}
    finally:
        _restore_config(path)


def test_load_server_params_missing_mcpservers_key_returns_empty():
    path = _set_config({"someOtherKey": {}})
    try:
        assert mcp_bridge.load_server_params() == {}
    finally:
        _restore_config(path)


def test_load_server_params_valid_file_parses_with_repo_root_cwd():
    config = {"mcpServers": {"test-server": {"command": "python3", "args": ["a.py", "b"]}}}
    path = _set_config(config)
    try:
        result = mcp_bridge.load_server_params()
        assert result == {
            "test-server": {
                "command": "python3",
                "args": ["a.py", "b"],
                "cwd": mcp_bridge._REPO_ROOT,
            }
        }, result
    finally:
        _restore_config(path)


def test_load_server_params_entry_missing_command_is_skipped():
    config = {"mcpServers": {"broken": {"args": ["a.py"]}}}
    path = _set_config(config)
    try:
        assert mcp_bridge.load_server_params() == {}
    finally:
        _restore_config(path)


# ── Prefixing ─────────────────────────────────────────────────────────────

def test_prefixed_name_normalizes_hyphen_to_underscore():
    assert mcp_bridge._prefixed_name("policy-library", "read_document") == "mcp__policy_library__read_document"


def test_server_slug_normalizes_hyphen():
    assert mcp_bridge._server_slug("policy-library") == "policy_library"


# ── Fake MCPClient for get_mcp_tool_defs / call_mcp_tool tests ──────────────
#
# Stands in for mcp_client.MCPClient without spawning a real subprocess.
# Class-level state (not instance-level) since mcp_bridge constructs a fresh
# client per operation — tests configure the class before invoking mcp_bridge
# functions and read back `calls` to assert how many connection attempts
# were made (the cache-retry tests' whole point).


class _FakeTool:
    def __init__(self, name, description="A test tool.", input_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class _FakeMCPClient:
    connect_error: Exception | None = None
    tools: list = []
    call_result: types.CallToolResult | None = None
    calls: list = []

    def __init__(self, command, args, env=None, cwd=None):
        self.command = command
        self.args = args
        self.cwd = cwd

    async def connect(self):
        _FakeMCPClient.calls.append((self.command, self.args))
        if _FakeMCPClient.connect_error is not None:
            raise _FakeMCPClient.connect_error

    async def cleanup(self):
        pass

    async def list_tools(self):
        return _FakeMCPClient.tools

    async def call_tool(self, tool_name, tool_input):
        return _FakeMCPClient.call_result


def _install_fake_client():
    _FakeMCPClient.connect_error = None
    _FakeMCPClient.tools = []
    _FakeMCPClient.call_result = None
    _FakeMCPClient.calls = []
    orig = mcp_bridge.MCPClient
    mcp_bridge.MCPClient = _FakeMCPClient
    return orig


def _restore_client(orig):
    mcp_bridge.MCPClient = orig


# ── get_mcp_tool_defs: prefixing + exclusion ────────────────────────────────

def test_get_mcp_tool_defs_prefixes_names_and_excludes_search_library():
    path = _set_config({"mcpServers": {"policy-library": {"command": "x", "args": []}}})
    orig_client = _install_fake_client()
    try:
        _FakeMCPClient.tools = [
            _FakeTool("search_library"),
            _FakeTool("read_document"),
            _FakeTool("list_documents"),
        ]
        defs = asyncio.run(mcp_bridge.get_mcp_tool_defs())
        names = {d["name"] for d in defs}
        assert names == {"mcp__policy_library__read_document", "mcp__policy_library__list_documents"}, names
        for d in defs:
            assert "input_schema" in d and "description" in d, d
    finally:
        _restore_client(orig_client)
        _restore_config(path)


# ── Cache distinction: no servers vs. all servers failed ────────────────────

def test_cache_populated_when_no_servers_configured():
    path = _set_config({"mcpServers": {}})
    try:
        defs1 = asyncio.run(mcp_bridge.get_mcp_tool_defs())
        assert defs1 == []
        assert mcp_bridge._tool_cache is not None, "expected the empty result to be cached"

        # Rewrite the underlying file to add a server -- must NOT be picked
        # up, proving the empty result really was served from cache rather
        # than by re-parsing an (still-empty) config each time.
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"new-server": {"command": "x", "args": []}}}, f)
        defs2 = asyncio.run(mcp_bridge.get_mcp_tool_defs())
        assert defs2 == [], "cached empty result should not have been invalidated by the file change"
    finally:
        _restore_config(path)


def test_cache_not_populated_when_all_configured_servers_fail():
    path = _set_config({"mcpServers": {"policy-library": {"command": "x", "args": []}}})
    orig_client = _install_fake_client()
    try:
        _FakeMCPClient.connect_error = RuntimeError("connection refused")
        defs1 = asyncio.run(mcp_bridge.get_mcp_tool_defs())
        assert defs1 == []
        assert mcp_bridge._tool_cache is None, "a total connect failure must not be cached"
        assert len(_FakeMCPClient.calls) == 1

        # Second call must retry (not serve a cached empty result) -- this is
        # the transient-failure-recovers-next-turn guarantee.
        defs2 = asyncio.run(mcp_bridge.get_mcp_tool_defs())
        assert len(_FakeMCPClient.calls) == 2, "expected a second connection attempt (no caching on total failure)"

        # Now the server "comes back up" -- next call should succeed and cache.
        _FakeMCPClient.connect_error = None
        _FakeMCPClient.tools = [_FakeTool("list_documents")]
        defs3 = asyncio.run(mcp_bridge.get_mcp_tool_defs())
        assert len(defs3) == 1
        assert mcp_bridge._tool_cache is not None
    finally:
        _restore_client(orig_client)
        _restore_config(path)


# ── is_mcp_tool ───────────────────────────────────────────────────────────

def test_is_mcp_tool_true_for_known_name_false_for_unknown():
    mcp_bridge.reset_cache()
    mcp_bridge._tool_cache = {
        "defs": [],
        "index": {"mcp__policy_library__read_document": ("policy-library", "read_document")},
    }
    try:
        assert mcp_bridge.is_mcp_tool("mcp__policy_library__read_document") is True
        assert mcp_bridge.is_mcp_tool("mcp__policy_library__nonexistent") is False
        assert mcp_bridge.is_mcp_tool("search_documents") is False
    finally:
        mcp_bridge.reset_cache()


def test_is_mcp_tool_false_when_cache_never_populated():
    mcp_bridge.reset_cache()
    assert mcp_bridge.is_mcp_tool("mcp__policy_library__read_document") is False


# ── call_mcp_tool ─────────────────────────────────────────────────────────

def _install_cache_and_client(call_result):
    mcp_bridge.reset_cache()
    mcp_bridge._tool_cache = {
        "defs": [],
        "index": {"mcp__policy_library__read_document": ("policy-library", "read_document")},
    }
    orig_client = _install_fake_client()
    _FakeMCPClient.call_result = call_result
    orig_load = mcp_bridge.load_server_params
    mcp_bridge.load_server_params = lambda: {
        "policy-library": {"command": "x", "args": [], "cwd": "/tmp"}
    }
    return orig_client, orig_load


def _teardown_cache_and_client(orig_client, orig_load):
    _restore_client(orig_client)
    mcp_bridge.load_server_params = orig_load
    mcp_bridge.reset_cache()


def test_call_mcp_tool_unknown_name_raises_value_error():
    mcp_bridge.reset_cache()
    raised = None
    try:
        asyncio.run(mcp_bridge.call_mcp_tool("mcp__nope__thing", {}))
    except ValueError as exc:
        raised = exc
    assert raised is not None, "expected ValueError for an unregistered prefixed name"
    assert "Unknown MCP tool" in str(raised), raised


def test_call_mcp_tool_is_error_result_raises_runtime_error():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="doc_id not found")], isError=True
    )
    orig_client, orig_load = _install_cache_and_client(result)
    try:
        raised = None
        try:
            asyncio.run(mcp_bridge.call_mcp_tool("mcp__policy_library__read_document", {"doc_id": "x"}))
        except RuntimeError as exc:
            raised = exc
        assert raised is not None, "expected RuntimeError when result.isError is True"
        assert "doc_id not found" in str(raised), raised
    finally:
        _teardown_cache_and_client(orig_client, orig_load)


def test_call_mcp_tool_joins_multiple_text_content_blocks():
    result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="part one"),
            types.TextContent(type="text", text="part two"),
        ],
        isError=False,
    )
    orig_client, orig_load = _install_cache_and_client(result)
    try:
        text = asyncio.run(mcp_bridge.call_mcp_tool("mcp__policy_library__read_document", {"doc_id": "x"}))
        assert text == "part one\npart two", text
    finally:
        _teardown_cache_and_client(orig_client, orig_load)


def test_call_mcp_tool_truncates_at_50000_chars():
    long_text = "x" * 60_000
    result = types.CallToolResult(content=[types.TextContent(type="text", text=long_text)], isError=False)
    orig_client, orig_load = _install_cache_and_client(result)
    try:
        text = asyncio.run(mcp_bridge.call_mcp_tool("mcp__policy_library__read_document", {"doc_id": "x"}))
        suffix = "... [truncated]"
        assert text.endswith(suffix), text[-50:]
        assert len(text) == 50_000 + len(suffix), len(text)
        assert text[:50_000] == long_text[:50_000]
    finally:
        _teardown_cache_and_client(orig_client, orig_load)


# ── rag_service wiring ───────────────────────────────────────────────────

def _run_answer_with_mcp_fakes(fake_defs: list[dict]):
    """Drive rag_service.answer_question with get_mcp_tool_defs and
    stream_chat_with_tools both faked (route_query/Retriever also faked so
    no live API calls or DB queries happen). Returns captured {"system",
    "tools"} the fake stream_chat_with_tools was invoked with.

    Patches are applied to rag_service's own module attributes (not
    mcp_bridge's) since rag_service imported these names directly via
    `from services.mcp_bridge import get_mcp_tool_defs, ...` — the same
    module-binding gotcha test_query_router.py's wiring tests already
    account for with route_query/stream_chat_with_tools.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    captured: dict = {}

    async def fake_get_mcp_tool_defs():
        return fake_defs

    async def fake_route_query(question, chat_history=None):
        # Faked so this test never makes a live Anthropic API call --
        # matches test_query_router.py's wiring-test convention.
        return "general"

    async def fake_stream_chat_with_tools(messages, system="", tools=None, tool_executor=None, **kwargs):
        captured["system"] = system
        captured["tools"] = tools
        if False:  # pragma: no cover -- makes this an async generator
            yield

    class FakeRetriever:
        def retrieve(self, query, top_k=5, doc_ids=None):
            return []

    orig_get_defs = rag_service.get_mcp_tool_defs
    orig_route_query = rag_service.route_query
    orig_stream = rag_service.stream_chat_with_tools
    orig_retriever = retriever_module.Retriever
    rag_service.get_mcp_tool_defs = fake_get_mcp_tool_defs
    rag_service.route_query = fake_route_query
    rag_service.stream_chat_with_tools = fake_stream_chat_with_tools
    retriever_module.Retriever = FakeRetriever
    try:
        async def collect():
            return [e async for e in rag_service.answer_question("q?", None, 5, db)]
        asyncio.run(collect())
    finally:
        rag_service.get_mcp_tool_defs = orig_get_defs
        rag_service.route_query = orig_route_query
        rag_service.stream_chat_with_tools = orig_stream
        retriever_module.Retriever = orig_retriever
        db.close()

    return captured


def test_wiring_mcp_tools_appended_and_system_mentions_mcp():
    fake_def = {
        "name": "mcp__policy_library__read_document",
        "description": "d",
        "input_schema": {"type": "object", "properties": {}},
    }
    captured = _run_answer_with_mcp_fakes([fake_def])
    assert fake_def in captured["tools"], captured["tools"]
    assert "MCP" in captured["system"]
    assert "mcp__policy_library__read_document" in captured["system"]
    assert "mcp__policy_library__list_documents" in captured["system"]


def test_wiring_empty_mcp_defs_matches_native_baseline_and_no_mcp_text():
    captured = _run_answer_with_mcp_fakes([])
    expected_tools = [
        rag_service.SEARCH_DOCUMENTS_TOOL,
        *rag_service.REMINDER_TOOLS,
        rag_service.TEXT_EDITOR_TOOL,
        rag_service.WEB_SEARCH_TOOL,
    ]
    assert captured["tools"] == expected_tools, captured["tools"]
    assert "MCP" not in captured["system"]


# ── End-to-end: real mcp_server.py subprocess ────────────────────────────
#
# Same style as test_mcp_client.py: spawns the real backend/mcp_server.py
# and talks to it through the real (unmocked) mcp_bridge functions. Uses
# sys.executable directly (not the bash -c wrapper the real .mcp.json uses)
# for portability, and has the subprocess os.chdir into the backend
# directory itself before running the server (mcp_bridge.load_server_params
# always sets the Popen cwd to the repo root, matching the real .mcp.json's
# "cd backend && ..." convention -- the chdir here reproduces that same
# effect without depending on bash being on PATH) so the server's
# cwd-relative data paths (data/research.db, etc.) resolve correctly.
#
# Skips cleanly (prints SKIP, still counts as passing) if the venv/dev data
# this depends on isn't available in the environment running the tests.

def test_e2e_real_server_list_tools_and_call_list_documents():
    wrapper = (
        "import os, runpy; "
        f"os.chdir({_BACKEND_DIR!r}); "
        f"runpy.run_path({_SERVER_SCRIPT!r}, run_name='__main__')"
    )
    config = {
        "mcpServers": {
            "policy-library": {
                "command": sys.executable,
                "args": ["-c", wrapper],
            }
        }
    }
    path = _set_config(config)
    try:
        try:
            defs = asyncio.run(mcp_bridge.get_mcp_tool_defs())
        except Exception as exc:
            print(f"  SKIP  (real mcp_server.py subprocess unavailable: {exc})")
            return
        if not defs:
            # get_mcp_tool_defs swallows per-server connect failures rather
            # than raising -- an empty result here means the subprocess
            # itself couldn't start (e.g. no venv in this environment), not
            # a bug in mcp_bridge.
            print("  SKIP  (real mcp_server.py subprocess produced no tools)")
            return

        names = {d["name"] for d in defs}
        assert names == {
            "mcp__policy_library__read_document",
            "mcp__policy_library__list_documents",
        }, names

        text = asyncio.run(mcp_bridge.call_mcp_tool("mcp__policy_library__list_documents", {}))
        assert text, "expected non-empty text from list_documents"
    finally:
        _restore_config(path)


# ── Test runner ───────────────────────────────────────────────────────────────

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
    print("\nRunning mcp_bridge.py tests...\n")

    _run("load_server_params: missing file -> {}", test_load_server_params_missing_file_returns_empty)
    _run("load_server_params: malformed JSON -> {}", test_load_server_params_malformed_json_returns_empty)
    _run("load_server_params: no mcpServers key -> {}", test_load_server_params_missing_mcpservers_key_returns_empty)
    _run("load_server_params: valid file parses with repo-root cwd", test_load_server_params_valid_file_parses_with_repo_root_cwd)
    _run("load_server_params: entry missing command is skipped", test_load_server_params_entry_missing_command_is_skipped)

    _run("_prefixed_name normalizes hyphen to underscore", test_prefixed_name_normalizes_hyphen_to_underscore)
    _run("_server_slug normalizes hyphen", test_server_slug_normalizes_hyphen)

    _run("get_mcp_tool_defs: prefixes names and excludes search_library", test_get_mcp_tool_defs_prefixes_names_and_excludes_search_library)

    _run("cache: populated (and held) when no servers configured", test_cache_populated_when_no_servers_configured)
    _run("cache: NOT populated when all configured servers fail", test_cache_not_populated_when_all_configured_servers_fail)

    _run("is_mcp_tool: true for known name, false for unknown", test_is_mcp_tool_true_for_known_name_false_for_unknown)
    _run("is_mcp_tool: false when cache never populated", test_is_mcp_tool_false_when_cache_never_populated)

    _run("call_mcp_tool: unknown name raises ValueError", test_call_mcp_tool_unknown_name_raises_value_error)
    _run("call_mcp_tool: isError result raises RuntimeError", test_call_mcp_tool_is_error_result_raises_runtime_error)
    _run("call_mcp_tool: joins multiple TextContent blocks", test_call_mcp_tool_joins_multiple_text_content_blocks)
    _run("call_mcp_tool: truncates at 50,000 chars", test_call_mcp_tool_truncates_at_50000_chars)

    _run("wiring: MCP tools appended and system mentions MCP", test_wiring_mcp_tools_appended_and_system_mentions_mcp)
    _run("wiring: empty MCP defs matches native baseline, no MCP text", test_wiring_empty_mcp_defs_matches_native_baseline_and_no_mcp_text)

    _run("E2E: real server list_tools + call list_documents", test_e2e_real_server_list_tools_and_call_list_documents)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")

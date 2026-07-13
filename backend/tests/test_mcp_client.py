"""Tests for mcp_client.py — the generic MCPClient stdio wrapper, exercised
end to end against the real PolicyLibraryMCP server (mcp_server.py).

Each test spawns mcp_server.py as a real subprocess over stdio (cwd set to
the backend directory so its data paths resolve), talks to it through
MCPClient, and tears the subprocess down again — this is integration-level,
not a mock. Read-only against the dev DB (backend/data/research.db): no rows
are modified.

search_library is intentionally not exercised here — it loads the local
sentence-transformers embedding model (~10-20s) and that path is already
covered by tests/test_mcp_server.py, which calls the tool function directly
without the extra subprocess/round-trip overhead.

Run from the backend directory:
    ./venv/bin/python -m tests.test_mcp_client
"""
import asyncio
import json
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import mcp.types as types
from mcp_client import MCPClient
from database import SessionLocal
from models.document import Document
from pydantic import AnyUrl

_SERVER_SCRIPT = os.path.join(_BACKEND_DIR, "mcp_server.py")


def _with_client(coro_fn):
    """Open an MCPClient connected to the real mcp_server.py subprocess, run
    coro_fn(client), and tear the connection down again. coro_fn is an async
    callable taking the connected MCPClient.
    """

    async def _runner():
        async with MCPClient(
            command=sys.executable,
            args=[_SERVER_SCRIPT],
            cwd=_BACKEND_DIR,
        ) as client:
            return await coro_fn(client)

    return asyncio.run(_runner())


def _result_text(result: types.CallToolResult) -> str:
    return "\n".join(
        block.text for block in result.content if isinstance(block, types.TextContent)
    )


# ── list_tools ────────────────────────────────────────────────────────────

def test_list_tools_returns_the_three_registered_tools():
    async def _do(client):
        return await client.list_tools()

    tools = _with_client(_do)
    names = {t.name for t in tools}
    assert names == {"search_library", "read_document", "list_documents"}, names


def test_list_tools_have_descriptions_and_input_schema():
    async def _do(client):
        return await client.list_tools()

    tools = _with_client(_do)
    assert len(tools) == 3
    for tool in tools:
        assert tool.description and tool.description.strip(), tool.name
        assert tool.inputSchema, tool.name


# ── call_tool: list_documents ────────────────────────────────────────────

def test_call_tool_list_documents_contains_live_doc_id():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the dev DB"
        expected_id = doc.id
    finally:
        db.close()

    async def _do(client):
        return await client.call_tool("list_documents", {})

    result = _with_client(_do)
    assert not result.isError, _result_text(result)
    text = _result_text(result)
    assert expected_id in text, (expected_id, text[:500])


# ── call_tool: read_document error path ──────────────────────────────────

def test_call_tool_read_document_unknown_id_is_error_result():
    async def _do(client):
        return await client.call_tool("read_document", {"doc_id": "no-such-id"})

    result = _with_client(_do)
    # FastMCP converts a raised exception inside a tool into an error
    # CallToolResult (isError=True) rather than propagating it as a
    # client-side exception over the JSON-RPC transport.
    assert result.isError is True, result
    assert _result_text(result), "expected error message text in the result content"


# ── resources: docs://documents, docs://documents/{doc_id} ──────────────────
#
# MCPClient has no resource-specific helper methods yet (client-side resource
# support is a later lesson) — these go through client.session() directly,
# the same underlying mcp.ClientSession the tool-call tests above use via
# MCPClient.list_tools()/call_tool().

def test_list_resources_and_templates_include_docs_endpoints():
    async def _do(client):
        resources = await client.session().list_resources()
        templates = await client.session().list_resource_templates()
        return resources, templates

    resources, templates = _with_client(_do)

    resource_uris = {str(r.uri) for r in resources.resources}
    assert "docs://documents" in resource_uris, resource_uris

    template_uris = {t.uriTemplate for t in templates.resourceTemplates}
    assert "docs://documents/{doc_id}" in template_uris, template_uris


def test_read_resource_documents_returns_json_list_with_live_doc_id():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the dev DB"
        expected_id = doc.id
    finally:
        db.close()

    async def _do(client):
        return await client.session().read_resource(AnyUrl("docs://documents"))

    result = _with_client(_do)
    assert len(result.contents) == 1, result.contents
    content = result.contents[0]
    assert isinstance(content, types.TextResourceContents), content
    if content.mimeType is not None:
        assert content.mimeType == "application/json", content.mimeType

    entries = json.loads(content.text)
    assert isinstance(entries, list) and entries, entries
    ids = {entry["id"] for entry in entries}
    assert expected_id in ids, (expected_id, ids)


# ── session() before connect ─────────────────────────────────────────────

def test_session_before_connect_raises_connection_error():
    client = MCPClient(command=sys.executable, args=[_SERVER_SCRIPT], cwd=_BACKEND_DIR)
    raised = False
    try:
        client.session()
    except ConnectionError:
        raised = True
    assert raised, "expected ConnectionError when session() is called before connect()"


# ── Test runner ──────────────────────────────────────────────────────────────

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
    print("\nRunning mcp_client.py tests...\n")

    _run("list_tools returns the three registered tools", test_list_tools_returns_the_three_registered_tools)
    _run("list_tools have descriptions and input schema", test_list_tools_have_descriptions_and_input_schema)
    _run("call_tool list_documents contains live doc id", test_call_tool_list_documents_contains_live_doc_id)
    _run("call_tool read_document unknown id is error result", test_call_tool_read_document_unknown_id_is_error_result)
    _run("list_resources and list_resource_templates include docs endpoints", test_list_resources_and_templates_include_docs_endpoints)
    _run("read_resource docs://documents returns JSON list with live doc id", test_read_resource_documents_returns_json_list_with_live_doc_id)
    _run("session() before connect raises ConnectionError", test_session_before_connect_raises_connection_error)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")

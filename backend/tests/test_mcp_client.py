"""Tests for mcp_client.py — the generic MCPClient stdio wrapper, exercised
end to end against the real PolicyLibraryMCP server (mcp_server.py).

Each test spawns mcp_server.py as a real subprocess over stdio (cwd set to
the backend directory so its data paths resolve), talks to it through
MCPClient, and tears the subprocess down again — this is integration-level,
not a mock.

Self-contained: this runner creates its OWN temporary SQLite database and
seeds one indexed document plus one chunk into it (see the env block below),
so it needs nothing from backend/data/research.db and passes on a fresh
checkout with no data directory at all. The dev/production database is never
opened, let alone modified. Expected values are still looked up from the
database at test time rather than hardcoded, so the test bodies are unchanged
by the seeding.

search_library is intentionally not exercised here — it loads the local
sentence-transformers embedding model (~10-20s) and that path is already
covered by tests/test_mcp_server.py, which calls the tool function directly
without the extra subprocess/round-trip overhead. That is also why no vector
or lexical index is seeded here: nothing in this file searches.

Run from the backend directory:
    ./venv/bin/python -m tests.test_mcp_client
"""
import asyncio
import os
import sys
import tempfile

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── Temp database, set before `database` is imported ─────────────────────────
#
# Must be set first: database.py builds its engine at import time from
# get_settings().database_url, so an override applied afterwards has no
# effect. (os.environ outranks backend/.env in pydantic-settings, so this
# wins on a dev machine too, not just in CI where there is no .env.)
#
# It must also be a real FILE, not an in-memory "sqlite://" URL: every test
# below spawns mcp_server.py as a separate PROCESS, and an in-memory SQLite
# database is per-process — the child would open its own empty database and
# never see the document seeded here. Same gotcha as
# tests/test_data_scoping.py's _MIGRATION_DB_PATH.
#
# Setting it here is necessary but not sufficient: the child process does NOT
# inherit it automatically — see the env= argument in _with_client below.
_TEST_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="mcp-client-tests-"), "research.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

import mcp.types as types
from mcp.shared.exceptions import McpError
from mcp_client import MCPClient
import database
from database import Base, SessionLocal
# The whole models package (not just models.document): Document's user_id /
# org_id are foreign keys to users / organizations, and create_all below needs
# every referenced table registered on Base.metadata.
from models import Document, DocumentChunk

_SERVER_SCRIPT = os.path.join(_BACKEND_DIR, "mcp_server.py")


# ── Seed the temp database (once, at import) ─────────────────────────────────

_SEED_DOC_ID = "mcp-client-test-doc"
_SEED_TITLE = "Algorithmic Accountability Ordinance (test fixture)"
_SEED_CHUNK = (
    "This synthetic fixture stands in for an indexed policy document. It exists "
    "so the MCP tool, resource and prompt round-trips in this file have a real "
    "document row and a real chunk to return, without depending on the contents "
    "of any particular machine's library."
)


def _seed_temp_database() -> None:
    """Create the schema and insert one indexed document with one chunk.

    Rows are constructed directly through SQLAlchemy rather than run through
    the ingestion pipeline: nothing in this file searches, so the chunk only
    has to exist, not to be embedded or lexically indexed.
    """
    Base.metadata.create_all(bind=database.engine)

    db = SessionLocal()
    try:
        doc = Document(
            id=_SEED_DOC_ID,
            filename="algorithmic-accountability-ordinance.txt",
            title=_SEED_TITLE,
            source_type="web",
            page_count=1,
            word_count=len(_SEED_CHUNK.split()),
            status="indexed",
        )
        db.add(doc)
        db.add(
            DocumentChunk(
                document_id=_SEED_DOC_ID,
                chunk_index=0,
                content=_SEED_CHUNK,
                page_number=1,
                section_header="Introduction",
                token_count=len(_SEED_CHUNK) // 4,
            )
        )
        db.commit()

        # Loud failure here beats every test below failing with the misleading
        # "expected at least one indexed document".
        seeded = db.query(Document).filter(Document.status == "indexed").first()
        assert seeded is not None, f"seeding failed: no indexed document in {_TEST_DB_PATH}"
    finally:
        db.close()


_seed_temp_database()


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
            # env must be passed explicitly for DATABASE_URL to reach the
            # server process. MCPClient forwards env to the MCP SDK's
            # StdioServerParameters, and env=None there does NOT mean
            # "inherit the parent environment" (as it would with a bare
            # subprocess.Popen): the SDK substitutes
            # get_default_environment(), an allowlist of HOME/LOGNAME/PATH/
            # SHELL/TERM/USER. Everything else — DATABASE_URL included — is
            # dropped, so without this the child would fall back to
            # config.py's default data/research.db and these tests would
            # silently run against the dev machine's real library.
            env=dict(os.environ),
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
        assert doc is not None, "expected at least one indexed document in the seeded test fixture"
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
# Exercised through MCPClient's resource helpers (list_resources,
# list_resource_templates, read_resource) rather than client.session()
# directly.

def test_list_resources_and_templates_include_docs_endpoints():
    async def _do(client):
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        return resources, templates

    resources, templates = _with_client(_do)

    resource_uris = {str(r.uri) for r in resources}
    assert "docs://documents" in resource_uris, resource_uris

    template_uris = {t.uriTemplate for t in templates}
    assert "docs://documents/{doc_id}" in template_uris, template_uris


def test_read_resource_documents_returns_json_list_with_live_doc_id():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the seeded test fixture"
        expected_id = doc.id
    finally:
        db.close()

    async def _do(client):
        return await client.read_resource("docs://documents")

    entries = _with_client(_do)
    # read_resource already parses application/json content into Python
    # objects — no json.loads needed here.
    assert isinstance(entries, list) and entries, entries
    assert all(isinstance(entry, dict) for entry in entries), entries
    ids = {entry["id"] for entry in entries}
    assert expected_id in ids, (expected_id, ids)


def test_read_resource_document_by_id_returns_text_with_title():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the seeded test fixture"
        expected_id = doc.id
        expected_label = doc.title or doc.filename
    finally:
        db.close()

    async def _do(client):
        return await client.read_resource(f"docs://documents/{expected_id}")

    text = _with_client(_do)
    # text/plain content is returned as a plain str, not JSON-parsed.
    assert isinstance(text, str), text
    assert expected_label in text, (expected_label, text[:500])


def test_read_resource_document_unknown_id_raises_mcp_error():
    async def _do(client):
        return await client.read_resource("docs://documents/no-such-id")

    # Unlike call_tool (where FastMCP converts a raised exception into an
    # error CallToolResult with isError=True), reading a resource template
    # whose handler raises propagates as a client-side exception over the
    # JSON-RPC transport: mcp.shared.exceptions.McpError, wrapping the
    # server's ValueError("Unknown doc_id: ...") message. Empirically
    # verified — there is no error payload to inspect, the call itself
    # raises.
    raised = None
    try:
        _with_client(_do)
    except McpError as exc:
        raised = exc
    assert raised is not None, "expected McpError for an unknown template doc_id"
    assert "no-such-id" in str(raised), raised


# ── prompts: summarize_document, policy_brief ────────────────────────────────
#
# Exercised through MCPClient's prompt helpers (list_prompts, get_prompt)
# rather than client.session() directly.

def test_list_prompts_includes_both_prompts_with_arguments():
    async def _do(client):
        return await client.list_prompts()

    result = _with_client(_do)
    prompts = {p.name: p for p in result}
    assert {"summarize_document", "policy_brief"} <= set(prompts), prompts

    summarize = prompts["summarize_document"]
    assert summarize.description and summarize.description.strip()
    summarize_args = {a.name: a for a in (summarize.arguments or [])}
    assert set(summarize_args) == {"doc_id"}, summarize_args
    assert summarize_args["doc_id"].required, summarize_args

    brief = prompts["policy_brief"]
    assert brief.description and brief.description.strip()
    brief_args = {a.name: a for a in (brief.arguments or [])}
    assert set(brief_args) == {"topic"}, brief_args
    assert brief_args["topic"].required, brief_args


def test_get_prompt_summarize_document_returns_user_message():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the seeded test fixture"
        doc_id = doc.id
    finally:
        db.close()

    async def _do(client):
        return await client.get_prompt("summarize_document", {"doc_id": doc_id})

    messages = _with_client(_do)
    assert len(messages) == 1, messages

    message = messages[0]
    assert message.role == "user", message.role
    assert isinstance(message.content, types.TextContent), message.content
    assert doc_id in message.content.text, (doc_id, message.content.text[:300])
    assert "read_document" in message.content.text, message.content.text[:300]


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
    _run("read_resource docs://documents/{id} returns text with title", test_read_resource_document_by_id_returns_text_with_title)
    _run("read_resource docs://documents/{unknown id} raises McpError", test_read_resource_document_unknown_id_raises_mcp_error)
    _run("list_prompts includes both prompts with arguments", test_list_prompts_includes_both_prompts_with_arguments)
    _run("get_prompt summarize_document returns user message", test_get_prompt_summarize_document_returns_user_message)
    _run("session() before connect raises ConnectionError", test_session_before_connect_raises_connection_error)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")

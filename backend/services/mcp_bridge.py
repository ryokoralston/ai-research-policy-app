"""mcp_bridge.py — merges MCP-server tools into the library-chat tool loop
(services/rag_service.py's answer_question).

Single source of truth: the repo-root .mcp.json (Claude Code's project-scope
MCP registration file). Whatever server is registered there for Claude Code
to use is exactly what the app's chat can call too — no separate
registration to keep in sync.

anyio cancel-scope constraint (see mcp_client.py's MCPClient / the MCP SDK's
stdio_client): the cancel scopes stdio_client opens must be entered and
exited in the SAME asyncio task. That rules out a long-lived singleton
MCPClient shared across chat turns/requests (each runs in its own task under
FastAPI/uvicorn). So every operation here — listing a server's tools, calling
one — opens a fresh MCPClient, connects, does its one thing, and cleans up,
all inside a single coroutine call. The only thing that survives across
calls is the *schema* cache below (tool name/description/input_schema are
static for a server's process lifetime); the connection itself is never
reused.
"""
import json
import os
import sys

import mcp.types as types
from mcp_client import MCPClient

_SERVICES_DIR = os.path.dirname(os.path.abspath(__file__))  # backend/services
_BACKEND_DIR = os.path.dirname(_SERVICES_DIR)  # backend
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)  # repo root (parent of backend)

# Module attribute (not a constant baked into functions) so tests can point
# it at a temp fixture file via monkeypatching mcp_bridge.MCP_CONFIG_PATH.
MCP_CONFIG_PATH = os.path.join(_REPO_ROOT, ".mcp.json")

# Tools excluded per-server because they duplicate a tool already native to
# the chat loop. search_library duplicates search_documents's hybrid
# retrieval (vector + BM25 + reranking, see rag/retriever.py) — same
# underlying data and result shape, just reached via a different transport,
# so exposing both would just give Claude two names for one capability.
# read_document and list_documents are net-new capabilities the chat loop
# doesn't otherwise have (full-document reads, a library inventory), so
# those stay.
EXCLUDED_TOOLS: dict[str, set[str]] = {"policy-library": {"search_library"}}

TOOL_PREFIX = "mcp__"

# Truncation guard on call_mcp_tool's returned text — read_document can
# return an entire document. mcp_server.py already caps read_document at
# 50_000 chars server-side (its MAX_READ_DOCUMENT_CHARS), but this bridge
# doesn't rely on that: any tool from any MCP server gets the same cap here.
MAX_RESULT_CHARS = 50_000
_TRUNCATION_SUFFIX = "... [truncated]"


def _server_slug(server_name: str) -> str:
    """"-" -> "_" so the prefixed tool name only ever contains one kind of
    separator. Anthropic's tool-name charset (letters, digits, _, -) would
    actually allow "-" through unchanged, but a single convention is easier
    to parse back apart in is_mcp_tool/call_mcp_tool and in the frontend's
    display label.
    """
    return server_name.replace("-", "_")


def _prefixed_name(server_name: str, tool_name: str) -> str:
    return f"{TOOL_PREFIX}{_server_slug(server_name)}__{tool_name}"


def load_server_params() -> dict[str, dict]:
    """Parse MCP_CONFIG_PATH (.mcp.json, Claude Code's project-scope format)
    into {server_name: {"command", "args", "cwd"}}.

    Returns plain dicts rather than mcp.StdioServerParameters so callers
    (and tests) don't need the SDK's types just to read this config. cwd is
    always the repo root — the registered commands (e.g. "cd backend && exec
    ./venv/bin/python mcp_server.py") are written assuming that starting
    point.

    Never raises: a missing file, malformed JSON, or a file with no
    "mcpServers" key all just mean "no MCP servers configured" — chat must
    keep working with zero MCP tools either way, not crash.
    """
    try:
        with open(MCP_CONFIG_PATH, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return {}

    params: dict[str, dict] = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict) or "command" not in entry:
            continue
        params[name] = {
            "command": entry["command"],
            "args": entry.get("args", []),
            "cwd": _REPO_ROOT,
        }
    return params


# Module-level schema cache, populated once per process by get_mcp_tool_defs
# and reused so a chat turn doesn't pay a connect/list_tools/disconnect
# round trip to every configured MCP server on every single message.
#   "defs":  Anthropic tool-def dicts, ready to splice into the tools list.
#   "index": prefixed_name -> (server_name, bare_tool_name), used by
#            is_mcp_tool / call_mcp_tool to route a tool_use back to its
#            server without re-deriving the split from the name string.
_tool_cache: dict | None = None


async def get_mcp_tool_defs() -> list[dict]:
    """Return Anthropic tool-def dicts for every non-excluded tool across all
    configured MCP servers, using (and populating) the module-level cache.

    Caching policy: an empty result is cached ONLY when .mcp.json has no
    servers configured at all — that's a stable answer (changing it means
    editing .mcp.json and restarting the process anyway). If servers ARE
    configured but every single one fails to connect (server crashed,
    dependency missing, etc.), the empty result is deliberately NOT cached,
    so a transient failure — the server coming back up — is retried and
    recovers on the very next chat turn instead of being stuck toolless for
    the rest of the process's life.
    """
    global _tool_cache
    if _tool_cache is not None:
        return _tool_cache["defs"]

    server_params = load_server_params()
    if not server_params:
        _tool_cache = {"defs": [], "index": {}}
        return _tool_cache["defs"]

    defs: list[dict] = []
    index: dict[str, tuple[str, str]] = {}
    any_server_succeeded = False

    for server_name, params in server_params.items():
        client = MCPClient(params["command"], params["args"], cwd=params["cwd"])
        try:
            await client.connect()
            tools = await client.list_tools()
        except Exception as exc:
            # A down/misconfigured MCP server must not break chat for
            # everyone else — log one line and move on to the next server.
            print(
                f"mcp_bridge: failed to connect to MCP server {server_name!r}: {exc}",
                file=sys.stderr,
            )
            await client.cleanup()
            continue
        any_server_succeeded = True
        await client.cleanup()

        excluded = EXCLUDED_TOOLS.get(server_name, set())
        for tool in tools:
            if tool.name in excluded:
                continue
            prefixed = _prefixed_name(server_name, tool.name)
            defs.append({
                "name": prefixed,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            })
            index[prefixed] = (server_name, tool.name)

    if not any_server_succeeded:
        # See docstring: servers are configured but all failed -- return the
        # (empty) result without caching so the next call retries.
        return defs

    _tool_cache = {"defs": defs, "index": index}
    return defs


def is_mcp_tool(name: str) -> bool:
    """True only for a prefixed name actually present in the populated
    cache — NOT just an "mcp__"-prefix startswith check, so an unrecognized
    mcp__-looking name (typo, stale cache, hallucinated tool) correctly
    falls through to the caller's "unknown tool" handling instead of being
    routed into call_mcp_tool and failing there instead.
    """
    if _tool_cache is None:
        return False
    return name in _tool_cache["index"]


async def call_mcp_tool(prefixed_name: str, tool_input: dict) -> str:
    """Call one MCP tool by its prefixed name and return its text result.

    Opens a fresh MCPClient, connects, calls, and cleans up within this one
    coroutine call (see module docstring re: the anyio same-task
    constraint) — no connection is reused from get_mcp_tool_defs's caching
    pass.
    """
    if _tool_cache is None or prefixed_name not in _tool_cache["index"]:
        raise ValueError(f"Unknown MCP tool: {prefixed_name}")
    server_name, bare_name = _tool_cache["index"][prefixed_name]

    server_params = load_server_params()
    params = server_params.get(server_name)
    if params is None:
        # Server was removed from .mcp.json since the cache was populated.
        raise ValueError(f"Unknown MCP tool: {prefixed_name}")

    client = MCPClient(params["command"], params["args"], cwd=params["cwd"])
    try:
        await client.connect()
        result = await client.call_tool(bare_name, tool_input)
    finally:
        await client.cleanup()

    text = "\n".join(
        block.text for block in result.content if isinstance(block, types.TextContent)
    )
    if result.isError:
        # Propagated as a raised exception (not a returned error string) so
        # the chat loop's tool_executor wrapper (anthropic_client.py's
        # stream_chat_with_tools) converts it into an is_error=True
        # tool_result, letting Claude see the failure and self-correct
        # instead of treating the error text as a normal success payload.
        raise RuntimeError(text or "MCP tool call failed")

    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + _TRUNCATION_SUFFIX
    return text


def reset_cache() -> None:
    """Test-only: clear the module-level schema cache."""
    global _tool_cache
    _tool_cache = None

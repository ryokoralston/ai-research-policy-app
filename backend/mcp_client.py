"""MCPClient — a thin wrapper around the MCP SDK's ClientSession that manages
the stdio connection lifecycle (spawn the server subprocess, open the
read/write streams, initialize the session, and tear it all down again) so
callers don't have to juggle nested async context managers themselves.

This is a generic stdio MCP client — it isn't specific to any one server —
but the `__main__` harness below exercises it against this repo's own
mcp_server.py (the PolicyLibraryMCP FastMCP server, see that file's
docstring), spawned as a subprocess with cwd set to the backend directory so
that its data paths (data/chroma, data/bm25.db, backend/.env, ...) resolve
correctly regardless of where this script itself is invoked from.

Run directly:

    cd backend && ./venv/bin/python mcp_client.py

This connects to mcp_server.py over stdio, lists its tools, calls
list_documents() to sanity-check the round trip end to end, reads its
docs://documents resources, and exercises its prompts.
"""
import asyncio
import contextlib
import json
import os
import sys
from typing import Any

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl


class MCPClient:
    """Wraps ClientSession + stdio_client so callers get a connected session
    without managing an AsyncExitStack themselves.

    Usage:

        async with MCPClient(command="python", args=["server.py"]) as client:
            tools = await client.list_tools()
            result = await client.call_tool("some_tool", {"arg": "value"})

    or, without the context manager:

        client = MCPClient(command="python", args=["server.py"])
        await client.connect()
        try:
            ...
        finally:
            await client.cleanup()
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict | None = None,
        cwd: str | None = None,
    ):
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        self._exit_stack: contextlib.AsyncExitStack | None = None
        self._session: ClientSession | None = None

    def session(self) -> ClientSession:
        """Return the active ClientSession, or raise if not connected yet."""
        if self._session is None:
            raise ConnectionError(
                "Client session not initialized. Call connect() (or use "
                "MCPClient as an async context manager) before making requests."
            )
        return self._session

    async def connect(self) -> None:
        self._exit_stack = contextlib.AsyncExitStack()
        try:
            server_params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=self.env,
                cwd=self.cwd,
            )
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
        except BaseException:
            await self._exit_stack.aclose()
            self._exit_stack = None
            raise

    async def cleanup(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.cleanup()

    async def list_tools(self) -> list[types.Tool]:
        result = await self.session().list_tools()
        return result.tools

    async def call_tool(self, tool_name: str, tool_input: dict) -> types.CallToolResult | None:
        return await self.session().call_tool(tool_name, tool_input)

    async def list_resources(self) -> list[types.Resource]:
        result = await self.session().list_resources()
        return result.resources

    async def list_resource_templates(self) -> list[types.ResourceTemplate]:
        result = await self.session().list_resource_templates()
        return result.resourceTemplates

    async def read_resource(self, uri: str) -> Any:
        """Read one resource by URI and return its content already unpacked:
        a parsed Python object (list/dict/...) for application/json content,
        or a plain str for anything else (e.g. text/plain).

        The MCP SDK's ClientSession.read_resource() requires a pydantic
        AnyUrl, not a plain str — the caller here still passes a plain str
        URI; the AnyUrl conversion is an internal implementation detail.

        Raises ValueError if the resource's first content block isn't text
        (e.g. BlobResourceContents) — this client has no binary consumers
        yet, so an unsupported content type should fail loudly rather than
        be silently mishandled.
        """
        result = await self.session().read_resource(AnyUrl(uri))
        resource = result.contents[0]
        if isinstance(resource, types.TextResourceContents):
            if resource.mimeType == "application/json":
                return json.loads(resource.text)
            return resource.text
        raise ValueError(f"Unsupported resource content type: {type(resource).__name__}")

    async def list_prompts(self) -> list[types.Prompt]:
        result = await self.session().list_prompts()
        return result.prompts

    async def get_prompt(self, prompt_name: str, args: dict[str, str]) -> list[types.PromptMessage]:
        result = await self.session().get_prompt(prompt_name, args)
        return result.messages


async def main():
    server_script = os.path.join(_BACKEND_DIR, "mcp_server.py")
    async with MCPClient(
        command=sys.executable,
        args=[server_script],
        cwd=_BACKEND_DIR,
    ) as client:
        tools = await client.list_tools()
        print(f"Connected. Server exposes {len(tools)} tool(s):\n")
        for tool in tools:
            first_line = (tool.description or "").strip().splitlines()[0] if tool.description else ""
            print(f"  - {tool.name}: {first_line}")

        print("\nCalling list_documents()...\n")
        result = await client.call_tool("list_documents", {})
        text = "\n".join(
            block.text for block in result.content if isinstance(block, types.TextContent)
        )
        preview_lines = text.splitlines()[:5]
        for line in preview_lines:
            print(f"  {line}")

        print("\nReading docs://documents resource...\n")
        entries = await client.read_resource("docs://documents")
        print(f"  {len(entries)} entries")
        if entries:
            first = entries[0]
            print(f"  first: id={first['id']} title={first['title']!r}")

            print(f"\nReading docs://documents/{first['id']} resource...\n")
            doc_text = await client.read_resource(f"docs://documents/{first['id']}")
            for line in doc_text.splitlines()[:2]:
                print(f"  {line}")

            print("\nListing prompts...\n")
            prompts = await client.list_prompts()
            for prompt in prompts:
                first_line = (prompt.description or "").strip().splitlines()[0] if prompt.description else ""
                print(f"  - {prompt.name}: {first_line}")

            if prompts:
                print(f"\nGetting prompt 'summarize_document' with first doc...\n")
                messages = await client.get_prompt("summarize_document", {"doc_id": first["id"]})
                print(f"  message count: {len(messages)}")
                print(f"  first message role: {messages[0].role}")
                for line in messages[0].content.text.splitlines()[:2]:
                    print(f"  {line}")


if __name__ == "__main__":
    asyncio.run(main())

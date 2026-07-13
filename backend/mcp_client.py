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

This connects to mcp_server.py over stdio, lists its tools, and calls
list_documents() to sanity-check the round trip end to end.
"""
import asyncio
import contextlib
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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


if __name__ == "__main__":
    asyncio.run(main())

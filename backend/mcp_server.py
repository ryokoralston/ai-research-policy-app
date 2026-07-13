"""PolicyLibraryMCP — exposes the AI Policy Research App's indexed document
library over the Model Context Protocol (MCP), stdio transport.

Wraps the same hybrid retrieval stack (rag.retriever.Retriever: vector search
+ BM25 + Reciprocal Rank Fusion + cross-encoder reranking) and the SQLite
document/chunk tables (models.document.Document, DocumentChunk) that already
back the FastAPI app's document/chat routes, as three read-only MCP tools:

  - search_library(query, top_k) — hybrid retrieval over chunk content
  - read_document(doc_id)        — full text of one document, in chunk order
  - list_documents()             — one line per indexed document

Run directly:

    cd backend && ./venv/bin/python mcp_server.py

Claude Desktop config (~/Library/Application Support/Claude/claude_desktop_config.json)
or Claude Code's .mcp.json — same shape either way:

    {
      "mcpServers": {
        "policy-library": {
          "command": "/absolute/path/to/ai-research-policy-app/backend/venv/bin/python",
          "args": ["/absolute/path/to/ai-research-policy-app/backend/mcp_server.py"]
        }
      }
    }

Note on working directory: config.py loads backend/.env and resolves
data/chroma, data/bm25.db etc. relative to the process's cwd. The config
above doesn't set a "cwd" field (not all MCP clients support one) — instead
mcp_server.py inserts its own directory onto sys.path and, if you additionally
need cwd-relative paths to resolve correctly regardless of where the client
launches the process from, invoke it as
`cd /absolute/path/to/backend && ./venv/bin/python mcp_server.py` (e.g. via a
wrapper shell script) rather than relying on the client's cwd.

Stdout hygiene: stdio-transport MCP servers must keep stdout reserved for the
line-delimited JSON-RPC protocol stream — anything else printed to stdout
(library init banners, telemetry, stray prints) corrupts the stream and the
client fails to parse it. FastMCP is constructed with log_level="ERROR" to
keep its own logging quiet. The heavier imports (chromadb, sentence-
transformers) are deferred to first tool call rather than done at module
import time, and that lazy init is wrapped in contextlib.redirect_stdout so
that if any dependency in that chain ever writes init noise to stdout instead
of stderr, it's redirected rather than corrupting the protocol stream.
(Verified experimentally: chromadb's "Failed to send telemetry event ..."
message already goes to stderr, not stdout — the redirect is defensive
belt-and-suspenders, not a fix for an observed leak.)
"""
import contextlib
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from database import SessionLocal
from models.document import Document, DocumentChunk

mcp = FastMCP("PolicyLibraryMCP", log_level="ERROR")

MAX_SEARCH_CONTENT_CHARS = 1200
MAX_READ_DOCUMENT_CHARS = 50_000

# Lazy, module-level cache: constructing Retriever() loads the local
# sentence-transformers embedding model and (on first search) the
# cross-encoder reranker, ~10-20s combined — fine to pay once, not on
# every tool call, and not at import time (import must stay cheap/side-
# effect-free; the actual chromadb.PersistentClient(...) construction,
# which touches disk, happens inside VectorStore.__init__, not at import).
_retriever = None


def _get_retriever():
    global _retriever
    if _retriever is None:
        with contextlib.redirect_stdout(sys.stderr):
            from rag.retriever import Retriever
            _retriever = Retriever()
    return _retriever


def _doc_label(doc: Document) -> str:
    return doc.title or doc.filename


@mcp.tool(
    name="search_library",
    description=(
        "Hybrid search (vector + BM25 + cross-encoder reranking) over the AI "
        "Policy Research App's indexed document library. Returns numbered "
        "excerpts, each with the source document's title, page number, "
        "section header, and a trimmed content snippet. Each result includes "
        "a doc_id — pass it to read_document to fetch that source's full text."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
def search_library(
    query: str = Field(description="Natural-language search query."),
    top_k: int = Field(default=5, description="Number of results to return."),
) -> str:
    retriever = _get_retriever()
    chunks = retriever.retrieve(query, top_k=top_k)

    if not chunks:
        return f"No results found in the document library for: {query!r}"

    db = SessionLocal()
    try:
        doc_ids = {c.doc_id for c in chunks}
        docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()}

        lines = []
        for i, chunk in enumerate(chunks, start=1):
            doc = docs.get(chunk.doc_id)
            title = _doc_label(doc) if doc else "(unknown document)"
            page = f"p.{chunk.page_number}" if chunk.page_number is not None else "p.?"
            section = chunk.section_header or "(no section header)"
            content = chunk.content.strip()
            if len(content) > MAX_SEARCH_CONTENT_CHARS:
                content = content[:MAX_SEARCH_CONTENT_CHARS].rstrip() + " [...]"
            lines.append(
                f"{i}. {title} ({page}, {section}) — doc_id={chunk.doc_id}\n"
                f"   {content}"
            )
        return "\n\n".join(lines)
    finally:
        db.close()


@mcp.tool(
    name="read_document",
    description=(
        "Return the full text of one indexed document, assembled from its "
        "chunks in reading order and prefixed with title/filename/page-count "
        "metadata. Raises an error if doc_id is unknown or the document has "
        "no indexed chunks."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
def read_document(
    doc_id: str = Field(description="Document id, e.g. from a search_library result."),
) -> str:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            raise ValueError(f"Unknown doc_id: {doc_id!r}")
        if doc.status != "indexed":
            raise ValueError(
                f"Document {doc_id!r} ({_doc_label(doc)}) is not indexed "
                f"(status={doc.status!r})"
            )

        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        if not chunks:
            raise ValueError(
                f"Document {doc_id!r} ({_doc_label(doc)}) is marked indexed but has no chunks"
            )

        header = (
            f"Title: {_doc_label(doc)}\n"
            f"Filename: {doc.filename}\n"
            f"Pages: {doc.page_count if doc.page_count is not None else 'unknown'}\n"
            + "-" * 40 + "\n"
        )
        body = "\n\n".join(c.content for c in chunks)
        text = header + body
        if len(text) > MAX_READ_DOCUMENT_CHARS:
            text = text[:MAX_READ_DOCUMENT_CHARS].rstrip() + "\n\n[... truncated ...]"
        return text
    finally:
        db.close()


@mcp.tool(
    name="list_documents",
    description=(
        "List every indexed document in the library: one line per document "
        "with doc_id, title (or filename if untitled), page count, and word "
        "count."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
def list_documents() -> str:
    db = SessionLocal()
    try:
        docs = (
            db.query(Document)
            .filter(Document.status == "indexed")
            .order_by(Document.created_at)
            .all()
        )
        if not docs:
            return "No indexed documents in the library."
        lines = [
            f"{d.id} | {_doc_label(d)} | pages={d.page_count if d.page_count is not None else '?'} "
            f"| words={d.word_count if d.word_count is not None else '?'}"
            for d in docs
        ]
        return "\n".join(lines)
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run(transport="stdio")

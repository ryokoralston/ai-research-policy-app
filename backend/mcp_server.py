"""PolicyLibraryMCP — exposes the AI Policy Research App's indexed document
library over the Model Context Protocol (MCP), stdio transport.

Wraps the same hybrid retrieval stack (rag.retriever.Retriever: vector search
+ BM25 + Reciprocal Rank Fusion + cross-encoder reranking) and the SQLite
document/chunk tables (models.document.Document, DocumentChunk) that already
back the FastAPI app's document/chat routes, as three read-only MCP tools:

  - search_library(query, top_k) — hybrid retrieval over chunk content
  - read_document(doc_id)        — full text of one document, in chunk order
  - list_documents()             — one line per indexed document

It also exposes the same document library as two read-only MCP resources —
structured counterparts to the tools above, meant for programmatic use
(e.g. a client's @-mention autocomplete) rather than LLM/human reading:

  - docs://documents            (application/json) — JSON array of indexed
                                 documents, structured counterpart of list_documents
  - docs://documents/{doc_id}   (text/plain)        — full text of one document,
                                 structured counterpart of read_document

Finally, it defines two MCP prompts — server-authored instruction templates
that tell a client exactly how to use the tools above for a common task,
rather than leaving the caller to improvise an ad-hoc request:

  - summarize_document(doc_id) — read one document via read_document and
                                  produce a structured executive summary for
                                  policy staff
  - policy_brief(topic)        — search the library via search_library, read
                                  the strongest hits, and write a cited policy
                                  brief on the topic

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
from mcp.server.fastmcp.prompts import base
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


def _indexed_documents(db) -> list[Document]:
    """Indexed documents ordered by created_at — the query shared by the
    list_documents tool and the docs://documents resource, so both stay in
    sync as documents are added.
    """
    return (
        db.query(Document)
        .filter(Document.status == "indexed")
        .order_by(Document.created_at)
        .all()
    )


def _document_full_text(doc_id: str) -> str:
    """Full text of one indexed document: header (title/filename/page count)
    followed by its chunks in reading order, truncated to
    MAX_READ_DOCUMENT_CHARS. Shared by the read_document tool and the
    docs://documents/{doc_id} resource so both return identical text for the
    same doc_id.

    Raises ValueError if doc_id is unknown, the document isn't indexed, or
    it has no indexed chunks.
    """
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
    return _document_full_text(doc_id)


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
        docs = _indexed_documents(db)
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


# ── Resources ─────────────────────────────────────────────────────────────
#
# Structured counterparts to the tools above: same underlying data, but
# machine-shaped output (JSON / plain text without formatting) for clients
# that want to consume it programmatically — e.g. an @-mention autocomplete
# list, rather than text meant for an LLM or human to read. Only SQLite is
# touched here, same as list_documents/read_document; no retriever involved.


@mcp.resource(
    "docs://documents",
    mime_type="application/json",
    description=(
        "Structured list of every indexed document in the library — the "
        "programmatic counterpart of the list_documents tool. Returns a "
        "JSON array of objects ({id, title, pages, words}), one per "
        "document, in the same order as list_documents. Intended for "
        "programmatic consumers (e.g. @-mention autocomplete in a client "
        "UI) rather than LLM/human reading; use the list_documents tool for "
        "human/LLM-readable text."
    ),
)
def list_docs() -> list[dict]:
    db = SessionLocal()
    try:
        docs = _indexed_documents(db)
        return [
            {
                "id": d.id,
                "title": _doc_label(d),
                "pages": d.page_count,
                "words": d.word_count,
            }
            for d in docs
        ]
    finally:
        db.close()


@mcp.resource(
    "docs://documents/{doc_id}",
    mime_type="text/plain",
    description=(
        "Full text of one indexed document, assembled from its chunks in "
        "reading order and prefixed with title/filename/page-count "
        "metadata — the same content the read_document tool returns. "
        "Raises an error if doc_id is unknown or the document has no "
        "indexed chunks."
    ),
)
def fetch_doc(doc_id: str) -> str:
    return _document_full_text(doc_id)


# ── Prompts ───────────────────────────────────────────────────────────────
#
# Server-authored instruction templates: each one returns a fully-written
# user message that tells a client precisely which tools to call and how to
# structure the result, instead of leaving the caller to write an ad-hoc
# prompt from scratch. Pure string builders — no DB access, no retriever, at
# either import time or call time; the actual tool calls (read_document,
# search_library) happen when the client *acts on* the returned prompt, not
# when the prompt itself is rendered.


@mcp.prompt(
    name="summarize_document",
    description=(
        "Generate an executive summary of one indexed document, written for "
        "policy staff. Instructs the caller to fetch the document's full "
        "text via read_document, then produce a structured summary: "
        "overview, key findings, policy implications, and gaps/caveats."
    ),
)
def summarize_document_prompt(
    doc_id: str = Field(description="Id of the document to summarize"),
) -> list[base.Message]:
    prompt = f"""Use the read_document tool with doc_id="{doc_id}" to fetch the full text of this document from the policy library. If the tool reports an error (unknown id, not indexed, or no chunks), stop and state clearly that the document could not be read — do not fabricate a summary in its place.

Once you have the full text, write an executive summary for congressional policy staff who need to understand this document quickly and accurately. Structure it as:

1. **Overview** — one paragraph describing what the document is and its overall purpose or argument.
2. **Key findings** — 3 to 5 bullet points capturing the document's most important claims, results, or recommendations.
3. **Policy implications** — what this document suggests for policymakers: risks, opportunities, or actions it points toward.
4. **Gaps and caveats** — notable limitations, open questions, or things the document does not address.

Quote directly from the document sparingly, only when the exact wording matters (a specific commitment, statistic, or legal standard). Otherwise paraphrase. Keep the summary factual and grounded in the document's actual content — do not add outside knowledge or speculation."""
    return [base.UserMessage(prompt)]


@mcp.prompt(
    name="policy_brief",
    description=(
        "Generate a structured, cited policy brief on a topic by searching "
        "the document library. Instructs the caller to run search_library "
        "(reformulating the query if the first results are weak), "
        "optionally read_document on the strongest hits, then write "
        "background, current landscape, areas of agreement/disagreement, "
        "and open questions — citing each claim by source title and doc_id."
    ),
)
def policy_brief_prompt(
    topic: str = Field(description="Policy topic to brief on"),
) -> list[base.Message]:
    prompt = f"""Use the search_library tool to search the policy document library for: "{topic}". If the first results are weak or off-topic, reformulate the query (try synonyms, narrower or broader phrasing, related terms) and search again before giving up.

For the most relevant results, use read_document on their doc_id to pull additional context if the search excerpts alone don't give you enough to support a claim confidently.

Write a structured policy brief on "{topic}" for congressional staff, with these sections:

1. **Background** — what this topic is and why it matters, in plain language.
2. **Current landscape** — what the library's documents say about the topic today, organized by theme rather than by document.
3. **Areas of agreement and disagreement** — where sources in the library converge or conflict, naming which sources hold which position.
4. **Open questions** — what remains unresolved or under-addressed across the sources you found.

Cite every substantive claim with the source document's title and doc_id, e.g. "(Title, doc_id=...)". If the library has little or no coverage of some aspect of this topic, say so explicitly rather than inventing content to fill the gap."""
    return [base.UserMessage(prompt)]


if __name__ == "__main__":
    mcp.run(transport="stdio")

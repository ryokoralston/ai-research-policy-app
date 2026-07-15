"""Per-document Q&A with API-native citations.

Unlike rag_service.answer_question (multi-document, tool-based retrieval,
sentence-level [N] citations assigned by us), this module asks a question
about exactly ONE document and lets the Anthropic Messages API locate and
cite the supporting passages itself: the document is sent as a single
`document` content block with `"citations": {"enabled": true}`, and the API
returns citation spans (page-located for PDFs, char-located for plain text)
alongside the answer text — no beta header required.

Two source shapes, chosen by build_document_block:
  - "pdf":  the raw PDF bytes, base64-encoded — citations come back with
            start_page_number/end_page_number (1-indexed).
  - "text": the document's already-extracted/chunked text, joined in
            chunk_index order — citations come back with
            start_char_index/end_char_index.

The PDF path is only used within a size/page budget (mirrors rag_service.py's
MAX_FALLBACK_* guard style) and never for documents whose extracted text
carries rag_service.SCANNED_PDF_MARKER — those were already vision-
transcribed once at index time, so re-sending the raw scanned bytes here
would just redo that work on every question; the stored transcription is
the better source.
"""
import base64
import logging
import os
from typing import AsyncIterator

from sqlalchemy.orm import Session

from models import Document, DocumentChunk
from services.anthropic_client import sse_event, _load_ai_settings, _get_anthropic_client, _block_get, UNTRUSTED_CONTENT_GUARD
from services.rag_service import SCANNED_PDF_MARKER

logger = logging.getLogger(__name__)

# Guards on the native-PDF citations path: the whole PDF is base64-encoded and
# sent as a single document content block in one request, so it must stay
# within a sane upload size, and Claude's per-document citation quality/limits
# are tuned for documents of ordinary length. Beyond either bound we fall back
# to the plain-text chunk path instead (see build_document_block).
MAX_PDF_ASK_BYTES = 25 * 1024 * 1024  # 25 MB — matches routers/documents.py's upload cap
MAX_PDF_ASK_PAGES = 100  # generous vs. rag_service's MAX_FALLBACK_PAGES (50) — this path sends the real PDF, not a vision transcription, so it can afford more pages

# cited_text can be an arbitrarily long quoted span; trim it before it goes
# out over SSE / into the final citations list so one long citation can't
# dominate the payload.
MAX_CITED_TEXT_CHARS = 300

SYSTEM_PROMPT = (
    "Answer the user's question using only the content of the attached document. "
    "Be concise and direct — a few sentences unless the question needs more detail. "
    "Ground every claim in a specific passage of the document as you write, so each "
    "sentence can be attributed to the part of the document it came from. "
    "If the document does not contain the answer, say so explicitly rather than guessing."
    "\n\n" + UNTRUSTED_CONTENT_GUARD
)


def _pdf_document_block(pdf_bytes: bytes, title: str) -> dict:
    """Build a native PDF `document` content block with citations enabled.

    Factored out of build_document_block so the block shape (base64 encoding,
    field names) is unit-testable without touching the filesystem or DB.

    Carries its own ephemeral cache_control breakpoint: the question text
    block that follows it in the user message (see ask_document_with_citations)
    varies per call, but the document itself is re-sent byte-for-byte every
    time a user asks another question about the same doc — this breakpoint
    lets a second question within the TTL read the (system +) document prefix
    from cache instead of paying full price again.
    """
    encoded = base64.standard_b64encode(pdf_bytes).decode()
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
        "title": title,
        "citations": {"enabled": True},
        "cache_control": {"type": "ephemeral"},
    }


def _text_document_block(text: str, title: str) -> dict:
    """Build a plain-text `document` content block with citations enabled.

    Factored out of build_document_block for the same reason as
    _pdf_document_block — pure, unit-testable block construction. See that
    function's docstring for why cache_control is attached here too.
    """
    return {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": text},
        "title": title,
        "citations": {"enabled": True},
        "cache_control": {"type": "ephemeral"},
    }


def _pdf_within_ask_guards(doc: Document) -> bool:
    """True when `doc` is an on-disk PDF within the native-citations size/page
    budget (MAX_PDF_ASK_BYTES / MAX_PDF_ASK_PAGES).

    Pure check apart from the file-existence/size stat calls — no chunk
    fetch, no API call — so it's directly testable with a fake Document-like
    object (just needs .file_path and .page_count attributes) and a real or
    missing tmp file, without touching the real DB.
    """
    if not doc.file_path or not os.path.exists(doc.file_path):
        return False
    if os.path.getsize(doc.file_path) > MAX_PDF_ASK_BYTES:
        return False
    if (doc.page_count or 0) > MAX_PDF_ASK_PAGES:
        return False
    return True


def _ordered_chunk_text(doc_id: str, db: Session) -> str:
    """Join a document's stored DocumentChunk.content in chunk_index order,
    separated by blank lines — the source text for the plain-text citations
    path. Kept as its own function (rather than inlined in
    build_document_block) so the chunk fetch is a single injectable seam:
    tests can monkeypatch this function to avoid touching a real DB/session.
    """
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    return "\n\n".join(c.content for c in chunks)


def build_document_block(doc: Document, db: Session) -> tuple[dict, str]:
    """Build the single `document` content block to send for this doc, plus
    a "pdf" | "text" source_kind tag the caller uses to interpret citation
    locations (page numbers vs. char offsets).

    Chooses the native-PDF path only when the file is a real, on-disk PDF
    within MAX_PDF_ASK_BYTES/MAX_PDF_ASK_PAGES AND its extracted text was not
    produced by the scanned-PDF vision fallback (SCANNED_PDF_MARKER) — see
    module docstring for why scanned PDFs prefer their stored transcription.
    Every other case (txt/html/image docs, oversized/missing-file/scanned
    PDFs) uses the plain-text chunk path.
    """
    title = doc.title or doc.filename
    ext = os.path.splitext(doc.file_path or "")[1].lower()
    chunk_text: str | None = None

    if ext == ".pdf" and _pdf_within_ask_guards(doc):
        chunk_text = _ordered_chunk_text(doc.id, db)
        if not chunk_text.startswith(SCANNED_PDF_MARKER):
            with open(doc.file_path, "rb") as f:
                pdf_bytes = f.read()
            return _pdf_document_block(pdf_bytes, title), "pdf"

    if chunk_text is None:
        chunk_text = _ordered_chunk_text(doc.id, db)
    return _text_document_block(chunk_text, title), "text"


def _citation_payload(citation, index: int, source_kind: str) -> dict:
    """Pure mapping from one raw citations_delta.citation (SDK object or
    plain dict — via _block_get, same dual-access helper anthropic_client
    uses for content blocks) to the JSON-safe payload sent as the "citation"
    SSE event and stored in the final "complete" event's citations list.

    source_kind="pdf" citations are page-located (start_page_number/
    end_page_number, 1-indexed); source_kind="text" citations are
    char-located (start_char_index/end_char_index). cited_text is trimmed to
    MAX_CITED_TEXT_CHARS.
    """
    cited_text = _block_get(citation, "cited_text") or ""
    if len(cited_text) > MAX_CITED_TEXT_CHARS:
        cited_text = cited_text[:MAX_CITED_TEXT_CHARS].rstrip() + "…"

    payload = {
        "index": index,
        "cited_text": cited_text,
        "document_title": _block_get(citation, "document_title"),
        "source_kind": source_kind,
    }
    if source_kind == "pdf":
        payload["start_page_number"] = _block_get(citation, "start_page_number")
        payload["end_page_number"] = _block_get(citation, "end_page_number")
    else:
        payload["start_char_index"] = _block_get(citation, "start_char_index")
        payload["end_char_index"] = _block_get(citation, "end_char_index")
    return payload


async def ask_document_with_citations(doc_id: str, question: str, db: Session) -> AsyncIterator[str]:
    """Stream an answer to `question` about a single document, with
    API-native citations. SSE events:

      "start"    {"question": ..., "source_kind": "pdf"|"text"}
      "token"    {"text": ...}                          — one per text_delta
      "citation" {...}                                  — one per citations_delta (see _citation_payload)
      "complete" {"answer": ..., "citations": [...], "source_kind": ...}
      "error"    {"message": ...}                        — doc missing/not indexed, or API failure

    No adaptive thinking (unnecessary for a single-document lookup) and no
    tools — this is a plain one-shot document-citations request, not the
    multi-document agentic loop in rag_service.answer_question.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        yield sse_event("error", {"message": "Document not found"})
        return
    if doc.status != "indexed":
        yield sse_event("error", {"message": "Document is not indexed yet"})
        return

    document_block, source_kind = build_document_block(doc, db)

    ai_settings = _load_ai_settings()
    client = _get_anthropic_client(ai_settings)

    messages = [{
        "role": "user",
        "content": [document_block, {"type": "text", "text": question}],
    }]

    yield sse_event("start", {"question": question, "source_kind": source_kind})

    full_text = ""
    next_index = 0
    ordered_citations: list[dict] = []

    try:
        async with client.messages.stream(
            model=ai_settings["main_model"],
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            async for event in stream:
                if getattr(event, "type", None) != "content_block_delta":
                    continue
                delta = getattr(event, "delta", None)
                delta_type = getattr(delta, "type", None)

                if delta_type == "text_delta":
                    text = getattr(delta, "text", "") or ""
                    if text:
                        full_text += text
                        yield sse_event("token", {"text": text})
                elif delta_type == "citations_delta":
                    citation = getattr(delta, "citation", None)
                    if citation is None:
                        continue
                    next_index += 1
                    payload = _citation_payload(citation, next_index, source_kind)
                    ordered_citations.append(payload)
                    yield sse_event("citation", payload)

            # Verifiable cache signal (see Anthropic docs: cache_read_input_tokens).
            # Still inside the `async with` block — get_final_message() is only
            # valid while the stream context is open.
            final = await stream.get_final_message()
            u = getattr(final, "usage", None)
            if u is not None:
                logger.info(
                    "doc-qa usage: input=%s cache_read=%s cache_write=%s",
                    u.input_tokens, u.cache_read_input_tokens, u.cache_creation_input_tokens,
                )
    except Exception as exc:
        yield sse_event("error", {"message": str(exc)})
        return

    yield sse_event("complete", {
        "answer": full_text,
        "citations": ordered_citations,
        "source_kind": source_kind,
    })

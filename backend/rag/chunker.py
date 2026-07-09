"""PDF → hierarchical text chunks."""
import re
from dataclasses import dataclass


@dataclass
class TextChunk:
    content: str
    page_number: int
    section_header: str
    chunk_index: int
    token_count: int


# Token budget for the paragraph-grouping pass. Module-level so the sentence
# splitter (oversized-paragraph fallback) can share them with chunk_text.
TARGET_TOKENS = 500
MAX_TOKENS = 800
MIN_TOKENS = 100

_MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S")


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _is_markdown_heading(stripped: str) -> bool:
    """ATX-style Markdown heading: 1-6 leading '#'s, a space, then content."""
    return bool(_MD_HEADING_RE.match(stripped))


def _clean_markdown_heading(stripped: str) -> str:
    """Strip leading '#'s and surrounding emphasis marks from a Markdown heading.

    e.g. "## **Executive Summary**" -> "Executive Summary"
    """
    text = re.sub(r"^#{1,6}\s+", "", stripped).strip()
    text = re.sub(r"^[*_]+", "", text)
    text = re.sub(r"[*_]+$", "", text)
    return text.strip()


def _detect_heading(line: str) -> bool:
    """Heuristic: Markdown ATX heading, short ALL-CAPS line, or numbered section heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    if _is_markdown_heading(stripped):
        return True
    if re.match(r"^\d+[\.\)]\s+[A-Z]", stripped):
        return True
    words = stripped.split()
    if len(words) <= 8 and stripped == stripped.upper() and len(stripped) > 3:
        return True
    return False


def _split_oversized_paragraph(text: str) -> list[str]:
    """Split a paragraph too large for one chunk into sentence-grouped pieces.

    Greedily groups sentences up to TARGET_TOKENS per piece, with a 1-sentence
    overlap between consecutive pieces (adapts the lesson's chunk_by_sentence
    to a token budget). If the text has no sentence terminators (or is a
    single sentence), it is returned unsplit — this guarantees the caller
    always makes forward progress instead of looping forever trying to find
    a split point that doesn't exist.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]

    if len(sentences) <= 1:
        return [text]

    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = _approx_tokens(sentence)

        if current and current_tokens + sentence_tokens > TARGET_TOKENS:
            pieces.append(" ".join(current))
            # 1-sentence overlap into the next piece.
            current = current[-1:]
            current_tokens = _approx_tokens(current[0])

        current.append(sentence)
        current_tokens += sentence_tokens

    if current:
        pieces.append(" ".join(current))

    return pieces


def chunk_text(text: str, page_number: int = 1) -> list[TextChunk]:
    """Chunk a block of text into 400-600 token chunks with section awareness."""
    lines = text.split("\n")
    paragraphs: list[tuple[str, str]] = []  # (paragraph_text, section_header)
    current_section = "Introduction"
    current_para: list[str] = []
    in_fence = False

    for line in lines:
        stripped = line.strip()

        # Fenced code blocks: toggle fence state, but the fence delimiter
        # line itself is ordinary content (not special-cased further).
        if stripped.startswith("```"):
            in_fence = not in_fence
            current_para.append(stripped)
            continue

        if not in_fence and _detect_heading(line):
            if current_para:
                paragraphs.append(("\n".join(current_para).strip(), current_section))
                current_para = []
            if _is_markdown_heading(stripped):
                current_section = _clean_markdown_heading(stripped)
            else:
                current_section = stripped
        else:
            if not stripped:
                if current_para:
                    paragraphs.append(("\n".join(current_para).strip(), current_section))
                    current_para = []
            else:
                current_para.append(stripped)

    if current_para:
        paragraphs.append(("\n".join(current_para).strip(), current_section))

    # Group paragraphs into chunks of ~500 tokens
    chunks: list[TextChunk] = []
    current_chunk_paras: list[str] = []
    current_chunk_section = current_section
    current_tokens = 0

    for para_text, section in paragraphs:
        if not para_text:
            continue
        para_tokens = _approx_tokens(para_text)

        # Section-purity: never let a chunk (or its overlap) span a section
        # boundary. Flush what we have and start fresh with no overlap.
        if current_chunk_paras and section != current_chunk_section:
            if current_tokens >= MIN_TOKENS:
                chunk_str = "\n\n".join(current_chunk_paras)
                chunks.append(TextChunk(
                    content=chunk_str,
                    page_number=page_number,
                    section_header=current_chunk_section,
                    chunk_index=len(chunks),
                    token_count=_approx_tokens(chunk_str),
                ))
                current_chunk_paras = []
                current_tokens = 0
            # else: the pending chunk is still tiny (< MIN_TOKENS). Rather
            # than emit a dust-sized chunk at every small section, merge
            # across the boundary — the tradeoff is a little cross-section
            # content in exchange for avoiding near-empty, low-value chunks.

        # Oversized paragraph: split into sentence-grouped pieces instead of
        # emitting one giant chunk.
        if para_tokens > MAX_TOKENS:
            if current_chunk_paras:
                if current_tokens >= MIN_TOKENS:
                    chunk_str = "\n\n".join(current_chunk_paras)
                    chunks.append(TextChunk(
                        content=chunk_str,
                        page_number=page_number,
                        section_header=current_chunk_section,
                        chunk_index=len(chunks),
                        token_count=_approx_tokens(chunk_str),
                    ))
                else:
                    # Too small to stand alone — fold into the oversized
                    # paragraph's sentence split instead of dropping it.
                    para_text = "\n\n".join(current_chunk_paras + [para_text])
                current_chunk_paras = []
                current_tokens = 0

            for piece in _split_oversized_paragraph(para_text):
                chunks.append(TextChunk(
                    content=piece,
                    page_number=page_number,
                    section_header=section,
                    chunk_index=len(chunks),
                    token_count=_approx_tokens(piece),
                ))
            current_chunk_section = section
            continue

        if current_tokens + para_tokens > MAX_TOKENS and current_tokens >= MIN_TOKENS:
            chunk_text_str = "\n\n".join(current_chunk_paras)
            chunks.append(TextChunk(
                content=chunk_text_str,
                page_number=page_number,
                section_header=current_chunk_section,
                chunk_index=len(chunks),
                token_count=_approx_tokens(chunk_text_str),
            ))
            # Overlap: keep last paragraph (within-section only)
            current_chunk_paras = current_chunk_paras[-1:] + [para_text]
            current_chunk_section = section
            current_tokens = _approx_tokens("\n\n".join(current_chunk_paras))
        else:
            current_chunk_paras.append(para_text)
            current_chunk_section = section
            current_tokens += para_tokens

    if current_chunk_paras:
        chunk_text_str = "\n\n".join(current_chunk_paras)
        if _approx_tokens(chunk_text_str) >= MIN_TOKENS:
            chunks.append(TextChunk(
                content=chunk_text_str,
                page_number=page_number,
                section_header=current_chunk_section,
                chunk_index=len(chunks),
                token_count=_approx_tokens(chunk_text_str),
            ))

    return chunks


def chunk_html(html_content: str) -> tuple[list[TextChunk], int]:
    """Strip HTML tags and return (chunks, word_count)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    word_count = len(text.split())
    return chunk_text(text), word_count


def chunk_plain_text(text: str) -> tuple[list[TextChunk], int]:
    """Chunk plain text and return (chunks, word_count)."""
    return chunk_text(text), len(text.split())


def chunk_pdf(file_path: str) -> tuple[list[TextChunk], int, int]:
    """Parse PDF and return (chunks, page_count, word_count)."""
    import pdfplumber

    all_chunks: list[TextChunk] = []
    total_words = 0

    with pdfplumber.open(file_path) as pdf:
        page_count = len(pdf.pages)
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            total_words += len(text.split())
            page_chunks = chunk_text(text, page_number=page_num)
            # Re-index globally
            for chunk in page_chunks:
                chunk.chunk_index = len(all_chunks)
                all_chunks.append(chunk)

    return all_chunks, page_count, total_words

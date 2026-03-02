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


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _detect_heading(line: str) -> bool:
    """Heuristic: short ALL-CAPS line or numbered section heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    if re.match(r"^\d+[\.\)]\s+[A-Z]", stripped):
        return True
    words = stripped.split()
    if len(words) <= 8 and stripped == stripped.upper() and len(stripped) > 3:
        return True
    return False


def chunk_text(text: str, page_number: int = 1) -> list[TextChunk]:
    """Chunk a block of text into 400-600 token chunks with section awareness."""
    lines = text.split("\n")
    paragraphs: list[tuple[str, str]] = []  # (paragraph_text, section_header)
    current_section = "Introduction"
    current_para: list[str] = []

    for line in lines:
        if _detect_heading(line):
            if current_para:
                paragraphs.append(("\n".join(current_para).strip(), current_section))
                current_para = []
            current_section = line.strip()
        else:
            stripped = line.strip()
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
    TARGET_TOKENS = 500
    MAX_TOKENS = 800
    MIN_TOKENS = 100

    for para_text, section in paragraphs:
        if not para_text:
            continue
        para_tokens = _approx_tokens(para_text)

        if current_tokens + para_tokens > MAX_TOKENS and current_tokens >= MIN_TOKENS:
            chunk_text_str = "\n\n".join(current_chunk_paras)
            chunks.append(TextChunk(
                content=chunk_text_str,
                page_number=page_number,
                section_header=current_chunk_section,
                chunk_index=len(chunks),
                token_count=_approx_tokens(chunk_text_str),
            ))
            # Overlap: keep last paragraph
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

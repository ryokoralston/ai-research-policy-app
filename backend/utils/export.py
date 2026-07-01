"""Shared report/analysis export helpers: Markdown → plain text and PDF."""
import re


def markdown_to_plain(text: str) -> str:
    """Strip Markdown syntax for .txt export.

    Single source of truth for both report and analysis exports. Uses the
    superset rule set (including bullet normalization, which the analysis
    export historically lacked).
    """
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`{3}.*?`{3}', '', text, flags=re.DOTALL)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'^[-*]\s+', '- ', text, flags=re.MULTILINE)
    return text.strip()


def render_pdf(title: str, content: str) -> bytes:
    """Render a titled Markdown document as PDF bytes (title bar + rule + body)."""
    from utils.pdf_renderer import make_pdf, render_markdown

    pdf, font, left, usable_w = make_pdf()

    pdf.set_font(font, "B", 18)
    pdf.set_x(left)
    pdf.multi_cell(usable_w, 10, title)
    pdf.ln(2)
    pdf.set_draw_color(100, 120, 200)
    pdf.line(left, pdf.get_y(), left + usable_w, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(5)

    render_markdown(pdf, font, left, usable_w, content)

    return bytes(pdf.output())

"""
Markdown-aware PDF renderer using fpdf2.
Supports: headings (H1-H3), bold inline, bullet lists, horizontal rules, paragraphs.
Uses Hiragino font on macOS for Japanese support, when available. DejaVu Sans is
bundled with the repo (backend/assets/fonts/) and registered unconditionally so
that non-latin-1 characters (em dash, arrows, ≥, etc.) and non-macOS deployments
(e.g. Render/Linux, which has no Hiragino) never fall back to fpdf2's core
Helvetica font — Helvetica can only encode latin-1 and raises
FPDFUnicodeEncodingException on anything outside it.
"""
import os
import re
from pathlib import Path

_JP_FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_DEJAVU_REGULAR = _FONTS_DIR / "DejaVuSans.ttf"
_DEJAVU_BOLD = _FONTS_DIR / "DejaVuSans-Bold.ttf"


def make_pdf():
    """Return (FPDF instance, font_name, left_margin, usable_width)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    left, right = 20, 20
    pdf.set_left_margin(left)
    pdf.set_right_margin(right)
    pdf.set_top_margin(20)
    usable_w = pdf.w - left - right

    # DejaVu is always available (bundled in the repo) so every deployment can
    # render full Unicode. Prefer Hiragino when present (dev Mac) for nicer
    # Japanese glyph coverage.
    pdf.add_font("DejaVu", "", str(_DEJAVU_REGULAR))
    pdf.add_font("DejaVu", "B", str(_DEJAVU_BOLD))
    font = "DejaVu"

    if os.path.exists(_JP_FONT):
        pdf.add_font("JP", "",  _JP_FONT)
        pdf.add_font("JP", "B", _JP_FONT)
        font = "JP"

    return pdf, font, left, usable_w


def render_markdown(pdf, font: str, left: float, usable_w: float, text: str):
    """Render markdown text into the given FPDF object."""
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()

        # Empty line → small vertical gap
        if not stripped:
            pdf.ln(3)
            continue

        # H1
        if re.match(r'^# [^#]', line):
            pdf.set_font(font, "B", 15)
            pdf.set_x(left)
            pdf.multi_cell(usable_w, 9, stripped[2:])
            pdf.ln(2)

        # H2
        elif re.match(r'^## [^#]', line):
            pdf.set_font(font, "B", 13)
            pdf.set_x(left)
            pdf.multi_cell(usable_w, 8, stripped[3:])
            pdf.ln(1)

        # H3
        elif re.match(r'^### ', line):
            pdf.set_font(font, "B", 12)
            pdf.set_x(left)
            pdf.multi_cell(usable_w, 7, stripped[4:])
            pdf.ln(1)

        # Horizontal rule
        elif re.match(r'^[-*_]{3,}$', stripped):
            pdf.ln(2)
            pdf.set_draw_color(180, 180, 180)
            pdf.line(left, pdf.get_y(), left + usable_w, pdf.get_y())
            pdf.set_draw_color(0, 0, 0)
            pdf.ln(4)

        # Bullet
        elif re.match(r'^[-*+] ', line):
            content = stripped[2:]
            pdf.set_x(left + 4)
            _write_inline(pdf, font, 11, usable_w - 4, "· " + content)

        # Numbered list
        elif re.match(r'^\d+\. ', line):
            content = re.sub(r'^\d+\. ', '', stripped)
            pdf.set_x(left + 4)
            _write_inline(pdf, font, 11, usable_w - 4, content)

        # Paragraph
        else:
            pdf.set_x(left)
            _write_inline(pdf, font, 11, usable_w, stripped)


def _write_inline(pdf, font: str, size: float, usable_w: float, text: str):
    """Write a line of text supporting **bold** inline markers."""
    # Split on **bold** markers
    parts = re.split(r'\*\*(.+?)\*\*', text)
    if len(parts) == 1:
        # No bold — fast path
        pdf.set_font(font, "", size)
        pdf.multi_cell(usable_w, 6, text)
        return

    # Mixed bold/normal — use write() to stay on same line
    for i, part in enumerate(parts):
        if not part:
            continue
        style = "B" if i % 2 == 1 else ""
        pdf.set_font(font, style, size)
        pdf.write(6, part)
    pdf.ln()

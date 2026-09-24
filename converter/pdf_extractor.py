"""PDF text/table extraction with right-to-left (Hebrew) repair.

Uses ``pdfplumber`` for both free text and ruled tables.  Many Hebrew
statements store glyphs in *visual* order, so the extracted strings come out
reversed ("םולש" instead of "שלום").  ``fix_rtl_text`` re-runs the Unicode
bidi algorithm with an RTL base direction, which restores logical order.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Iterable, Literal

import pdfplumber

try:  # python-bidi >= 0.6 (Rust core)
    from bidi import get_display
except ImportError:  # pragma: no cover - older python-bidi
    from bidi.algorithm import get_display  # type: ignore

RtlMode = Literal["auto", "on", "off"]

_HEBREW_RE = re.compile(r"[֐-׿]")

# Common Hebrew statement header words.  If we see their *reversed* spelling
# more often than the correct one, the PDF was extracted in visual order.
_HEBREW_MARKERS = (
    "תאריך", "יתרה", "חובה", "זכות", "סכום", "פירוט", "תיאור",
    "אסמכתא", "פעולה", "ערך", "תנועות", "חשבון", "כרטיס", "עסקה",
)


@dataclass
class ExtractedDocument:
    """Text extracted from a statement PDF, one entry per page."""

    pages: list[str] = field(default_factory=list)
    has_hebrew: bool = False
    rtl_fixed: bool = False
    n_tables: int = 0

    @property
    def text(self) -> str:
        return "\n\n".join(
            f"--- Page {i} ---\n{p}" for i, p in enumerate(self.pages, start=1)
        )

    @property
    def n_pages(self) -> int:
        return len(self.pages)


def contains_hebrew(text: str) -> bool:
    return bool(_HEBREW_RE.search(text or ""))


def looks_reversed(text: str) -> bool:
    """Heuristic: are Hebrew marker words more often reversed than not?"""
    correct = sum(text.count(w) for w in _HEBREW_MARKERS)
    reversed_ = sum(text.count(w[::-1]) for w in _HEBREW_MARKERS)
    return reversed_ > correct


def fix_rtl_text(text: str) -> str:
    """Restore logical order for visually-ordered Hebrew lines.

    Lines without Hebrew characters are left untouched so numbers, dates and
    Latin descriptions keep their original order.
    """
    out: list[str] = []
    for line in text.splitlines():
        if contains_hebrew(line):
            out.append(get_display(line, base_dir="R"))
        else:
            out.append(line)
    return "\n".join(out)


_NUMERIC_CELL_RE = re.compile(r"^[\s\d.,\-()₪$€£%/]+$")


def _looks_like_header(cells: list[str]) -> bool:
    filled = [c for c in cells if c]
    return len(filled) >= 2 and not any(_NUMERIC_CELL_RE.match(c) for c in filled)


def _table_to_text(table: Iterable[Iterable[str | None]]) -> str:
    """Render a pdfplumber table for the LLM.

    When the first row is a header, every cell is emitted as ``Header: value``
    so a small model never has to count pipe positions to know which column
    an amount belongs to.  Empty cells become ``-``.
    """
    rows = []
    for row in table:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    header = rows[0] if _looks_like_header(rows[0]) else None
    out = []
    if header is not None:
        out.append(" | ".join(h or f"col{i}" for i, h in enumerate(header, start=1)))
        for cells in rows[1:]:
            pairs = []
            for i, (h, v) in enumerate(zip(header, cells), start=1):
                pairs.append(f"{h or f'col{i}'}: {v or '-'}")
            out.append(" | ".join(pairs))
    else:
        out = [" | ".join(c or "-" for c in cells) for cells in rows]
    return "\n".join(out)


def extract_pdf(file: bytes | io.BytesIO | str, rtl_mode: RtlMode = "auto") -> ExtractedDocument:
    """Extract text + tables from every page of a PDF.

    Parameters
    ----------
    file:
        Raw bytes, a file-like object or a path.
    rtl_mode:
        ``"auto"`` – detect reversed Hebrew and fix it when needed;
        ``"on"`` – always apply the bidi fix to Hebrew lines;
        ``"off"`` – never touch the text.
    """
    if isinstance(file, (bytes, bytearray)):
        file = io.BytesIO(file)

    doc = ExtractedDocument()
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # layout=True keeps column alignment, which helps the LLM
            # associate amounts with the right description.
            text = page.extract_text(layout=True) or ""
            tables = page.extract_tables() or []
            doc.n_tables += len(tables)
            table_text = "\n\n".join(_table_to_text(t) for t in tables if t)
            # collapse runs of spaces / blank lines that layout mode produces
            text = re.sub(r"[ \t]{3,}", "  ", text)
            text = re.sub(r"(?:[ \t]*\n){3,}", "\n\n", text)
            # Ruled tables are far less ambiguous than free text, so they go
            # first and the model is told to prefer them.
            if table_text.strip():
                text = f"[TABLES]\n{table_text}\n\n[TEXT]\n{text}"
            doc.pages.append(text.strip())

    full = "\n".join(doc.pages)
    doc.has_hebrew = contains_hebrew(full)

    apply_fix = rtl_mode == "on" or (rtl_mode == "auto" and doc.has_hebrew and looks_reversed(full))
    if apply_fix and doc.has_hebrew:
        doc.pages = [fix_rtl_text(p) for p in doc.pages]
        doc.rtl_fixed = True
    return doc

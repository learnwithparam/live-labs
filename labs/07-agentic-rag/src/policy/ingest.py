"""Turn PDFs into passages a citation can point at: one per section, with its doc, heading and page.

The policies arrive as wiki exports, so the text a search sees is what pypdf pulls out of each page.
Sizes tell a heading from body text and from the header and footer printed on every page.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

# Sizes the wiki export prints: running header and footer, document title, section heading. Body is 10.
FURNITURE, TITLE, HEADING = 8, 18, 14


@dataclass(frozen=True)
class Passage:
    id: str
    doc: str
    section: str
    page: int
    text: str


@dataclass
class Line:
    text: str
    size: float
    page: int


def lines(path: Path) -> list[Line]:
    """Every text run in reading order with its printed size, so headings and page furniture can be told apart."""
    found: list[Line] = []
    for number, page in enumerate(PdfReader(path).pages, 1):
        def visit(text, user_matrix, text_matrix, font, size, number=number):
            if text.strip():
                found.append(Line(text.strip(), round(size * text_matrix[0], 1), number))

        page.extract_text(visitor_text=visit)
    return found


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def by_section(path: Path) -> list[Passage]:
    """One passage per heading, with the header and footer dropped and wrapped lines joined."""
    passages, heading, page, body = [], None, 1, []
    doc = next((line.text for line in lines(path) if line.size == TITLE), path.stem)

    def close():
        if heading and body:
            text = re.sub(r"\s+", " ", " ".join(body)).strip()
            passages.append(Passage(f"{path.stem}#{slug(heading)}", doc, heading, page, text))

    for line in lines(path):
        # The header and footer repeat on every page. Left in, they would match every question a little.
        if line.size <= FURNITURE or line.size == TITLE:
            continue
        if line.size == HEADING:
            close()
            heading, page, body = line.text, line.page, []
        else:
            body.append(line.text)
    close()
    return passages


def ingest(docs: Path) -> list[Passage]:
    """Every section of every PDF in the folder, in file order."""
    return [passage for path in sorted(docs.glob("*.pdf")) for passage in by_section(path)]

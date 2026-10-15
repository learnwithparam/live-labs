"""Turn PDFs into passages a citation can point at: one per section, with its doc, heading and page."""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

# Sizes the wiki export prints: running header and footer, document title, section heading. Body is 10.
FURNITURE, TITLE, HEADING = 8, 18, 14


@dataclass
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


def join(parts: list[str]) -> str:
    """The PDF wraps a sentence, and a command, across lines; put them back on one line."""
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def by_section(path: Path) -> list[Passage]:
    """One passage per heading, the header and footer dropped, the wrapped lines joined."""
    passages, heading, page, body = [], None, 1, []
    doc = next((line.text for line in lines(path) if line.size == TITLE), path.stem)

    def close():
        if heading and body:
            passages.append(Passage(f"{path.stem}#{slug(heading)}", doc, heading, page, join(body)))

    for line in lines(path):
        # The header and footer repeat on every page. Left in, they would match every question a little.
        if line.size <= FURNITURE or line.size == TITLE:
            continue
        # A heading closes the passage before it and opens the next, so a passage never spans two topics
        # and its id names the section a person opens to check.
        if line.size == HEADING:
            close()
            heading, page, body = line.text, line.page, []
        else:
            body.append(line.text)
    close()
    return passages


def by_size(path: Path, size: int = 300) -> list[Passage]:
    """The obvious way: the text the PDF reader returns, cut every `size` characters, as most tutorials split."""
    passages = []
    for number, page in enumerate(PdfReader(path).pages, 1):
        text = page.extract_text()
        # The cut falls wherever the count lands: inside a command, or after the page header.
        for start in range(0, len(text), size):
            passages.append(Passage(f"{path.stem}#p{number}-{start // size + 1}", path.stem, f"characters {start} to {start + size}", number, text[start : start + size]))
    return passages


def ingest(folder: Path, how: str = "section") -> list[Passage]:
    split = by_section if how == "section" else by_size
    return [passage for path in sorted(folder.glob("*.pdf")) for passage in split(path)]

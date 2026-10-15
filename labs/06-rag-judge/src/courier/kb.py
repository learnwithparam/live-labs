"""The courier help centre as passages, one per `##` section, found by BM25 keyword search.

A section is the unit a support agent would link to, so it is also the unit the bot cites and the
grounding check reads. Keyword search is enough here: couriers use the help centre's own words
("instant payout", "Red Card"), and the lab is about what happens after retrieval, not before.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import bm25s


@dataclass(frozen=True)
class Passage:
    id: str
    article: str
    section: str
    text: str


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load(folder: Path) -> list[Passage]:
    passages = []
    for path in sorted(folder.glob("*.md")):
        title, *sections = path.read_text().split("\n## ")
        article = title.removeprefix("# ").strip()
        for section in sections:
            heading, _, body = section.partition("\n")
            # The id names the article and the section, so a citation reads as a help centre link.
            passages.append(Passage(f"{path.stem}/{slug(heading)}", article, heading.strip(), body.strip()))
    return passages


class HelpCentre:
    def __init__(self, passages: list[Passage]):
        self.passages = passages
        self.by_id = {passage.id: passage for passage in passages}
        # The heading goes into the index too: "Instant payout" is often the only words a courier types.
        corpus = [f"{passage.article} {passage.section} {passage.text}" for passage in passages]
        self.index = bm25s.BM25()
        self.index.index(bm25s.tokenize(corpus, stopwords="en", show_progress=False), show_progress=False)

    def search(self, question: str, top_k: int = 4) -> list[Passage]:
        tokens = bm25s.tokenize([question], stopwords="en", show_progress=False)
        found, scores = self.index.retrieve(tokens, k=min(top_k, len(self.passages)), show_progress=False)
        # BM25 scores 0 when no word is shared; those passages would only invite a guess.
        return [self.passages[index] for index, score in zip(found[0], scores[0]) if score > 0]

    def get(self, ids: list[str]) -> list[Passage]:
        return [self.by_id[passage_id] for passage_id in ids if passage_id in self.by_id]


def listing(passages: list[Passage]) -> str:
    # Each passage carries its id in brackets, so a model can name exactly the one it used.
    return "\n\n".join(f"[{passage.id}] {passage.article}: {passage.section}\n{passage.text}" for passage in passages)

"""The pipeline end to end: the passages, the index, and every question answered and judged."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from oncall.answer import Reply, answer
from oncall.ingest import ingest
from oncall.retrieve import Index
from oncall.score import Question, Verdict, judge, tally


class Desk:
    def __init__(self, docs: Path, client, model: str, how: str = "section", k: int = 4):
        self.passages = ingest(docs, how)
        self.index, self.client, self.model, self.k = Index(self.passages), client, model, k

    def ask(self, question: str) -> Reply:
        return answer(question, self.index.search(question, self.k), self.client, self.model)

    def run(self, questions: list[Question]) -> dict:
        with ThreadPoolExecutor(8) as pool:
            replies = list(pool.map(lambda q: self.ask(q.question), questions))
        verdicts = [judge(q, r) for q, r in zip(questions, replies)]
        rows = [row(q.question, r, v) for q, r, v in zip(questions, replies, verdicts)]
        return {"tally": asdict(tally(verdicts, replies)) | {"share": tally(verdicts, replies).share}, "rows": rows}


def row(question: str, reply: Reply, verdict: Verdict | None) -> dict:
    """One answer as the page shows it: the reply, every passage the search found, and the verdict.
    A question outside the set has no approved fact, so it carries no verdict rather than a guessed one."""
    found = [{"id": p.id, "doc": p.doc, "section": p.section, "page": p.page, "text": p.text} for p in reply.passages]
    checked = asdict(verdict) if verdict else {"supported": None, "reason": None, "fact": None, "cited_text": {}}
    return {"question": question, "answer": reply.answer, "cited": reply.cited, **checked, "found": found}

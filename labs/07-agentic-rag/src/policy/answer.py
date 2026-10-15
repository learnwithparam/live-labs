"""Write the answer from the sections found, the same way for plain RAG and for the agent.

Keeping one answer step means the comparison is fair: the agent only changes what is retrieved. The
model may say the sections do not cover the question; that answer goes to a person, not to a guess.
"""

from pydantic import BaseModel

from policy.llm import ask
from policy.search import Policies, listing

INSTRUCTIONS = (
    "You answer an on-call engineer at Ledgerline, a payments company, from its runbooks and policies. "
    "Use only the sections given. Answer every part of the question in short, direct sentences, with "
    "the exact commands, numbers, channels and time limits the sections give. Apply every rule that "
    "fits the situation, even one the question does not name; a rollback or a flag change is a change "
    "to production, so the deploy policy applies to it. Cite the ids in square brackets exactly as "
    "given, such as deploy-policy#freeze-window, for each section you used. If the sections do not cover part of the question, set covered to false and "
    "say which part is missing, rather than guess."
)


class Answer(BaseModel):
    answer: str
    cited: list[str]
    covered: bool


def write(question: str, ids: list[str], policies: Policies, client, model: str) -> tuple[dict, float]:
    sections = policies.get(ids)
    if not sections:
        # Nothing found means nothing to answer from; a person picks it up.
        return {"answer": "", "cited": [], "covered": False}, 0.0
    got, cost = ask(client, model, INSTRUCTIONS, f"Sections:\n\n{listing(sections)}\n\nQuestion: {question}", Answer)
    if got is None:
        return {"answer": "", "cited": [], "covered": False}, cost
    # A citation the model made up is dropped: the page links each one to a real section.
    cited = [passage_id for passage_id in got.cited if passage_id in policies.by_id]
    return {"answer": got.answer, "cited": cited, "covered": got.covered}, cost

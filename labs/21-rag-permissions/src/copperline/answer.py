"""Answer a question from company documents, with the permission check in one of three places.

- `naive` retrieves across everything and asks the model not to reveal what the user may not see. The
  model cannot know who may open what, and the secret is already in its context.
- `post` retrieves first, then batch-checks every chunk's document with OpenFGA and drops the denied ones.
- `pre` asks OpenFGA for every document the user may view, and retrieval searches only those.

Either OpenFGA mode keeps a denied chunk out of the prompt, which is the only place a leak can be stopped.
"""

import re

from copperline.config import PRICES
from copperline.retrieve import Index

MODES = ("naive", "post", "pre")
SHOWN = 5  # chunks the model reads
OVERFETCH = 15  # the post-filter retrieves more, so dropping denied chunks still leaves enough to answer
NO_ANSWER = "No document you can open answers this."
RULES = ("Answer the question from the sources below and nothing else. Cite every fact with its source id in "
         f"square brackets, like [SUP-311]. If the sources do not answer it, reply exactly: {NO_ANSWER}")


def gather(mode: str, user: str, question: str, index: Index, authorizer) -> dict:
    """The chunks the model will read for this user, and how many the permission check removed."""
    if mode == "naive":
        return {"chunks": index.search(question, SHOWN), "dropped": 0, "checked": 0}
    if mode == "pre":
        # One list-objects call up front. Cheap while a user can open hundreds of documents, slow at millions.
        viewable = authorizer.viewable(user)
        return {"chunks": index.search(question, SHOWN, only=viewable), "dropped": 0, "checked": len(viewable)}
    found = index.search(question, OVERFETCH)
    # One batch check covers every document the retriever touched; nothing reaches the model unchecked.
    allowed = authorizer.allowed(user, [chunk["document"] for chunk in found])
    kept = [chunk for chunk in found if chunk["document"] in allowed]
    return {"chunks": kept[:SHOWN], "dropped": len(found) - len(kept), "checked": len({chunk["document"] for chunk in found})}


def prompt(mode: str, person: dict, question: str, chunks: list[dict]) -> list[dict]:
    rules = RULES
    if mode == "naive":
        # The approach this lab replaces: the rule lives in the prompt, and the model is trusted to keep it.
        rules += (f"\n\nThe person asking is {person['name']}, {person['title']}, in the groups "
                  f"{', '.join(person['groups'])}. Some sources are confidential. Do not reveal information "
                  "from any document this person is not allowed to see.")
    sources = "\n\n".join(f"[{chunk['document']}] {chunk['title']} ({chunk['source']}, {chunk['place']})\n{chunk['text']}"
                          for chunk in chunks) or "(no sources)"
    return [{"role": "system", "content": rules},
            {"role": "user", "content": f"Sources:\n\n{sources}\n\nQuestion: {question}"}]


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed}


async def answer(client, model: str, mode: str, person: dict, question: str, chunks: list[dict]) -> dict:
    response = await client.chat.completions.create(
        model=model, temperature=0, messages=prompt(mode, person, question, chunks),
        extra_body={"usage": {"include": True}})
    text = (response.choices[0].message.content or "").strip()
    in_context = {chunk["document"] for chunk in chunks}
    # A citation counts only when it names a document the model was given; anything else is invented.
    cited = [document for document in dict.fromkeys(re.findall(r"\[([A-Za-z0-9-]+)\]", text)) if document in in_context]
    return {"text": text, "cited": cited, **usage_of(response, model)}

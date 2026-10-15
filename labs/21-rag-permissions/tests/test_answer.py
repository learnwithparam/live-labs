"""Where the check sits: the post-filter and pre-filter keep a denied chunk out of the prompt; naive does not."""

import asyncio

import pytest
from fakes import FakeAuthorizer, FakeOpenAI, cite_everything

from copperline.answer import answer, gather, prompt
from copperline.leaks import pairs
from copperline.retrieve import Index, chunks_of
from copperline.sync import documents, people

INDEX = Index(chunks_of(documents()))
EVERY_DOCUMENT = {document["id"] for document in documents()}


def denying(pair: dict) -> FakeAuthorizer:
    """Everyone may open everything except the pair's document, which its denied user may not."""
    return FakeAuthorizer({pair["denied"]: EVERY_DOCUMENT - {pair["document"]}, pair["allowed"]: EVERY_DOCUMENT})


@pytest.mark.parametrize("pair", pairs(), ids=lambda pair: pair["id"])
def test_naive_retrieval_puts_the_denied_document_in_the_prompt(pair):
    # This is the leak the lab is about: the retriever finds the document whatever the person may open.
    found = gather("naive", pair["denied"], pair["question"], INDEX, None)
    assert pair["document"] in {chunk["document"] for chunk in found["chunks"]}


@pytest.mark.parametrize("mode", ["post", "pre"])
@pytest.mark.parametrize("pair", pairs(), ids=lambda pair: pair["id"])
def test_both_openfga_modes_keep_the_denied_document_out(pair, mode):
    denied = gather(mode, pair["denied"], pair["question"], INDEX, denying(pair))
    assert pair["document"] not in {chunk["document"] for chunk in denied["chunks"]}
    allowed = gather(mode, pair["allowed"], pair["question"], INDEX, denying(pair))
    assert pair["document"] in {chunk["document"] for chunk in allowed["chunks"]}


def test_the_post_filter_checks_every_document_the_retriever_touched_in_one_call():
    pair = pairs()[0]
    authorizer = denying(pair)
    found = gather("post", pair["denied"], pair["question"], INDEX, authorizer)
    assert len(authorizer.calls) == 1 and found["dropped"] >= 1
    assert found["checked"] == len(authorizer.calls[0][2])


def test_only_the_naive_prompt_asks_the_model_to_keep_secrets():
    person = people()["ravi"]
    assert "Do not reveal" in prompt("naive", person, "q", [])[0]["content"]
    assert "Do not reveal" not in prompt("post", person, "q", [])[0]["content"]


def test_a_citation_counts_only_when_the_model_was_given_that_document():
    chunks = [chunk for chunk in INDEX.chunks if chunk["document"] == "SUP-311"][:1]
    model = FakeOpenAI(lambda messages: "Refunds take five days [SUP-311] [drv-board-pack-q3].")
    reply = asyncio.run(answer(model, "fake", "post", people()["sam"], "q", chunks))
    assert reply["cited"] == ["SUP-311"]
    assert reply["cost"] == 0.0


def test_the_fake_model_cites_only_what_it_was_shown():
    pair = pairs()[0]
    found = gather("post", pair["denied"], pair["question"], INDEX, denying(pair))
    model = FakeOpenAI(cite_everything)
    reply = asyncio.run(answer(model, "fake", "post", people()[pair["denied"]], pair["question"], found["chunks"]))
    assert pair["document"] not in reply["text"]

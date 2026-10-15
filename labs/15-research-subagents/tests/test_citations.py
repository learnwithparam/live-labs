"""The check in code: a claim is only as good as the quote that sits, character for character, in its source."""

from kitebird.citations import find_quote, locate, score
from kitebird.corpus import load_brief, load_sources

SOURCES = load_sources()
QUOTE = "Private households can receive up to 1,000 euros toward an electric cargo bike, and small businesses up to 2,500 euros."


def claim(quotes, source="S09"):
    return {"text": "Berlin pays up to 1,000 euros.", "source": source, "quotes": quotes}


def test_a_quote_that_is_in_the_source_is_found_and_highlighted():
    found = find_quote(claim([QUOTE]), SOURCES)
    assert found["verdict"] == "quoted"
    start, end = found["spans"][0]
    assert SOURCES["S09"].text[start:end].lower() == QUOTE.lower().rstrip(".")


def test_curly_quotes_and_spacing_do_not_count_as_a_changed_quote():
    assert locate("Berlin reopened  its cargo bike grant on 1 April 2026", SOURCES["S09"])
    assert locate("“Berlin reopened its cargo bike grant on 1 April 2026.”", SOURCES["S09"])


def test_a_changed_number_is_a_missing_quote_with_the_nearest_sentence():
    found = find_quote(claim([QUOTE.replace("1,000", "1,500")]), SOURCES)
    assert found["verdict"] == "quote missing"
    assert found["closest"].startswith("Private households")


def test_a_quote_from_another_source_names_where_it_really_is():
    found = find_quote(claim([QUOTE], source="S10"), SOURCES)
    assert found["verdict"] == "quote missing"
    assert "in S09, not in S10" in found["reason"]


def test_a_short_quote_or_no_source_proves_nothing():
    assert find_quote(claim(["the grant"]), SOURCES)["verdict"] == "quote missing"
    assert find_quote(claim([QUOTE], source=None), SOURCES)["verdict"] == "quote missing"
    assert find_quote(claim([]), SOURCES)["verdict"] == "quote missing"


def test_every_quote_must_be_there_not_just_one():
    assert find_quote(claim([QUOTE, "Berlin pays every rider 9,000 euros a year."]), SOURCES)["verdict"] == "quote missing"


def test_score_counts_a_question_covered_only_by_a_supported_claim():
    claims = [{"source": "S09", "verdict": "supported"}, {"source": "S11", "verdict": "not supported"},
              {"source": "S06", "verdict": "quote missing"}]
    result = score(claims, load_brief())
    assert (result["supported"], result["claims"], result["editor"]) == (1, 3, 2)
    assert result["covered"] == ["grants"]
    assert result["share"] == 0.333

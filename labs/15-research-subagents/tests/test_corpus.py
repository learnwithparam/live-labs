"""The library: the search an agent depends on finds the right source, and the traps stay traps."""

from kitebird.corpus import Library, load_brief, load_sources


def test_every_aspect_of_the_brief_names_sources_the_library_holds():
    sources = load_sources()
    for aspect in load_brief()["aspects"]:
        assert aspect["sources"] and set(aspect["sources"]) <= set(sources), aspect


def test_search_ranks_the_source_that_answers_first():
    library = Library()
    assert library.search("cargo bike grant Berlin")[0]["source"] == "S09"
    assert library.search("dealer margin spare parts")[0]["source"] == "S06"


def test_the_company_name_alone_finds_a_trap_first():
    # A kite school shares the name: an agent that searches only "Kitebird" reads the wrong source.
    assert Library().search("Kitebird")[0]["source"] == "S30"


def test_read_returns_the_whole_source_and_rejects_unknown_ids():
    library = Library()
    assert "3 million euros" in library.read("s09")
    assert library.read("S99").startswith("No source")

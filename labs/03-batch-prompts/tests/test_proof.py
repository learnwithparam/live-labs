"""The lab's promise, against a real model: packing products under one copy of the instructions cuts the
cost per product by at least 30% and keeps the attributes as right as one product per request."""

from pathlib import Path

import pytest

from catalog.cli import client_and_model
from catalog.serve import Shelf

LAB = Path(__file__).parent.parent
MAX_COST_RATIO = 0.7  # packed per 1,000 products, against one per request
MIN_MATCH = 0.95  # of the attributes the retailer supplied
SLACK = 3  # attributes packed may lose against one per request


@pytest.mark.proof
def test_packing_cuts_the_cost_and_keeps_the_attributes(tmp_path):
    client, model = client_and_model()
    shelf = Shelf(LAB / "data/catalogue.csv", LAB / "data/retailer-attributes.csv", client, model, tmp_path)
    one, packed = shelf.fill("one"), shelf.fill("packed")
    ratio = packed["per_thousand"] / one["per_thousand"]
    assert ratio <= MAX_COST_RATIO, f"{model}: packed costs {ratio:.0%} of one per request"
    assert packed["right"] >= one["right"] - SLACK, f"{model}: packed {packed['right']}, one per request {one['right']}"
    assert packed["right"] / packed["total"] >= MIN_MATCH, f"{model}: packed {packed['right']}/{packed['total']}"
    assert packed["person"] and packed["filled"] + len(packed["person"]) == len(shelf.products)

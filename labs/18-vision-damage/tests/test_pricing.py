"""Pricing is plain code over the rate table, so its numbers can be checked by hand."""

from saltmarsh.claims import claim, rates, truth
from saltmarsh.pricing import merge, price


def reading(photo, findings, usable=True):
    return {"photo": photo, "usable": usable, "findings": findings}


def finding(panel, action="repair", severity="minor"):
    return {"panel": panel, "action": action, "severity": severity, "prior_damage": False}


def test_a_replaced_wing_and_a_repaired_bumper_price_from_the_table():
    estimate = price([finding("front_left_wing", "replace", "severe"), finding("front_bumper")],
                     claim("SM-1041")["vehicle"], rates())
    # Wing: £150 part + 2.0h at £52 + paint (£85 + 2.2h at £52). Bumper: 1.0h + paint.
    assert [line["total"] for line in estimate["lines"]] == [453.40, 251.40]
    assert estimate["total"] == 704.80
    assert estimate["share_of_value"] == round(704.80 / 5200, 3)


def test_the_same_panel_on_two_photos_is_one_line_and_the_worse_reading_wins():
    merged = merge([reading("P01", [finding("front_left_wing")]),
                    reading("P02", [finding("front_left_wing", "replace", "severe")])])
    assert len(merged) == 1
    assert merged[0]["action"] == "replace" and merged[0]["photos"] == ["P01", "P02"]


def test_an_unusable_photo_adds_nothing_to_the_estimate():
    assert merge([reading("P10", [finding("bonnet")], usable=False)]) == []


def test_a_lamp_is_replaced_even_when_the_reading_says_repair():
    estimate = price([finding("headlight_left")], claim("SM-1047")["vehicle"], rates())
    assert estimate["lines"][0]["action"] == "replace" and estimate["lines"][0]["overridden"]


def test_every_bill_is_within_tolerance_of_the_confirmed_findings_priced():
    """The truth file is consistent with the table: a perfect reading would pass every billed claim."""
    adjuster, table = truth(), rates()
    for claim_id, row in adjuster["claims"].items():
        if "bill" not in row:
            continue
        found = claim(claim_id)
        readings = [{"photo": photo_id, **adjuster["photos"][photo_id]} for photo_id in found["photos"]]
        total = price(merge(readings), found["vehicle"], table)["total"]
        assert abs(total - row["bill"]) <= table["tolerance"] * row["bill"], claim_id

import pytest

from lucca.till import OrderProblem, Till, check


def test_the_till_resolves_the_callers_own_words_and_fills_in_the_default_crust():
    till = Till()
    pizza = till.add_item("pepperoni", "large", ["thin crust"])
    wings = till.add_item("buffalo wings", "dozen")
    pie = till.add_item("margarita", "medium", ["mushroom"])
    assert (pizza.item, pizza.size, pizza.options) == ("pepperoni-pizza", "large", ["thin crust"])
    assert (wings.item, wings.size, wings.options) == ("wings", "12 piece", ["buffalo"])
    assert pie.options == ["mushrooms", "regular crust"]
    assert till.total()["subtotal"] == pizza.each + wings.each + pie.each


def test_an_option_said_twice_is_one_choice():
    line = Till().add_item("garlic parmesan wings", "6 piece", ["garlic parm"])
    assert line.options == ["garlic parmesan"]


def test_the_till_takes_the_ways_a_real_model_phrased_items_on_the_recorded_calls():
    soda = Till().add_item("Diet Coke", "2 liter")
    wings = Till().add_item("barbecue wings", "six pieces")
    assert Till().add_item("cheese pizzas", "small", quantity=2).item == "cheese-pizza"
    cheese = Till().add_item("cheese pizza", "large", ["cheese", "extra cheese"])
    assert (soda.item, soda.size, soda.options) == ("soda-2l", None, ["diet coke"])
    assert (wings.size, wings.options) == ("6 piece", ["bbq"])
    assert cheese.options == ["extra cheese", "regular crust"]
    sausage = Till().add_item("Sausage and peppers pizza", "large", ["sausage", "peppers"])
    assert sausage.options == ["regular crust"]
    # A name the till cannot place comes back with the menu, so the model retries before staff.
    with pytest.raises(OrderProblem, match="^'pizza' does not name.*Sausage and peppers pizza.*add_item again"):
        Till().add_item("pizza", "large", ["sausage", "peppers"])


@pytest.mark.parametrize("item, size, options, says", [
    ("family special", None, [], "send_to_staff"),
    ("pepperoni", None, [], "needs a size"),
    ("wings", "6 piece", [], "needs a sauce"),
    ("cheese", "large", ["thin", "gluten free"], "takes one crust"),
    ("cheese", "large", ["pineapple"], "send_to_staff"),
])
def test_the_till_refuses_what_it_cannot_key_and_says_what_to_do_next(item, size, options, says):
    with pytest.raises(OrderProblem, match=says):
        Till().add_item(item, size, options)


def test_fifty_pizzas_is_a_catering_job_for_staff_not_a_phone_order():
    Till().add_item("cheese", "large", quantity=20)
    with pytest.raises(OrderProblem, match="more than a phone order takes.*send_to_staff"):
        Till().add_item("cheese", "large", quantity=50)


def test_a_placed_or_handed_off_ticket_is_shut():
    placed = Till()
    placed.add_item("cheese", "small")
    placed.place_order("tom.", 412)
    assert placed.name == "Tom"
    with pytest.raises(OrderProblem, match="already in the kitchen"):
        placed.add_item("cannoli")
    staff = Till()
    staff.send_to_staff("half and half pizza")
    with pytest.raises(OrderProblem, match="already with staff"):
        staff.place_order("Karen", 413)


def test_only_an_exact_match_with_the_keyed_order_counts():
    keyed = {"name": "Tom", "lines": [{"item": "cheese-pizza", "size": "small", "options": ["regular crust"], "quantity": 2}]}
    till = Till()
    till.add_item("cheese", "small")
    till.add_item("cheese pizza", "small")
    till.place_order("Tom", 412)
    assert check(till.ticket(), keyed) == (True, "matches the keyed order")
    wrong = Till()
    wrong.add_item("cheese", "small", ["thin"], 2)
    wrong.place_order("Tom", 413)
    ok, why = check(wrong.ticket(), keyed)
    assert not ok and "missing 2 small cheese-pizza +regular crust" in why
    assert check(Till().ticket(), {"staff": "half and half"})[0] is False

"""Lucca Brick Oven's till: the menu, the order ticket the agent fills in, and the check against the order
a person keyed from the same call.

The till, not the model, decides what can be ordered. The model passes the caller's own words ("a dozen
buffalo wings") and the till resolves them against the menu or refuses with the next step. A voice agent
that guesses a near match keys a pizza nobody asked for, and the kitchen bakes it."""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MENU_FILE = Path("data/menu.json")
FILLER_WORDS = {"a", "an", "and", "with", "on", "of", "order"}  # words around an option, not part of it
MOST_OF_ONE_ITEM = 20  # a phone order for 50 pizzas is a catering job, and staff take those


class OrderProblem(Exception):
    """A refusal the model reads. Its message always ends with what to do next, so the model asks the
    caller or hands the call to staff instead of trying again with a guess."""


def plain(text: str) -> str:
    # Speech to text writes "Gluten-free", "gluten free" or "gluten free," for the same words, and a
    # model writes "cheese pizzas" for two of the menu's "cheese pizza", so plurals compare as singular.
    words = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower().replace("-", " ")).split()
    return " ".join(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
                    for word in words)


def staff_next(what: str) -> str:
    return (f"{what} Do not offer a substitute. Call send_to_staff with this reason, then tell the caller a "
            "team member will call them right back.")


class Menu:
    """The menu as the till reads it. Every name the caller may say maps to exactly one item or option."""

    def __init__(self, data: dict | None = None):
        self.data = data or json.loads(MENU_FILE.read_text())
        self.items = {item["id"]: item for item in self.data["items"]}
        self.groups = self.data["groups"]

    def hint(self) -> str:
        """The menu as one line of text for speech to text to expect, so it spells menu words as the
        till does: "cannolis", not "canoles"."""
        return f"{self.data['restaurant']}: " + ", ".join(item["name"] for item in self.data["items"]) + "."

    def find_item(self, said: str) -> tuple[dict, list[str]]:
        """The item a caller named, plus any option said as part of its name. Callers say "buffalo
        wings", "thin crust pepperoni" and "a two liter Diet Coke", so a name that holds an item's alias,
        with the rest of the words naming one of that item's options, is that item with that option."""
        wanted = plain(said)
        for item in self.data["items"]:
            if wanted in self.names(item):
                return item, []
        for item in self.data["items"]:
            for name in sorted(self.names(item), key=len, reverse=True):
                if f" {name} " not in f" {wanted} ":
                    continue
                rest = " ".join(word for word in f" {wanted} ".replace(f" {name} ", " ", 1).split()
                                if word not in FILLER_WORDS)
                if rest and self.has_option(item, rest):
                    return item, [rest]
        # The model may have said it its own way ("pizza" with sausage and peppers), so the till names
        # the menu and lets it try again before anything goes to staff.
        menu = ", ".join(item["name"] for item in self.data["items"])
        raise OrderProblem(f"'{said}' does not name one {self.data['restaurant']} menu item. The menu has: "
                           f"{menu}. If the caller's words name one of these, call add_item again with that "
                           "name and the caller's size and options. Only if they name none of these: "
                           + staff_next("the menu cannot hold it."))

    def names(self, item: dict) -> set[str]:
        return {plain(alias) for alias in [item["name"], *item["aliases"]]}

    def has_option(self, item: dict, said: str) -> bool:
        try:
            return self.find_option(item, said) is not None
        except OrderProblem:
            return False

    def find_size(self, item: dict, said: str | None) -> str | None:
        if "sizes" not in item:
            return None
        sizes = list(item["sizes"])
        if not said:
            # A pizza without a size cannot be priced or baked, so the caller is asked, never assumed.
            raise OrderProblem(f"{item['name']} needs a size. Ask the caller: {', '.join(sizes)}.")
        wanted = plain(said).removesuffix(" piece")
        spoken = {plain(word): size for word, size in item.get("size_words", {}).items()}
        for size in sizes:
            if plain(said) == plain(size) or wanted == plain(size).split()[0]:
                return size
        if wanted in spoken:
            return spoken[wanted]
        raise OrderProblem(f"{item['name']} comes in {', '.join(sizes)}, not '{said}'. Ask the caller which size.")

    def find_option(self, item: dict, said: str) -> tuple[str, str] | None:
        wanted = plain(said)
        if any(set(wanted.split()) <= set(name.split()) for name in self.names(item)):
            # "A large cheese with extra cheese", or sausage and peppers on the sausage and peppers
            # pizza: words that only repeat the item's own name are the item, not a topping on it.
            return None
        for group_name in item["groups"]:
            for option, details in self.groups[group_name]["options"].items():
                if wanted in {plain(option), *(plain(alias) for alias in details["aliases"])}:
                    return group_name, option
        offered = [option for group_name in item["groups"] for option in self.groups[group_name]["options"]]
        choices = f" Options are: {', '.join(offered)}." if offered else " It takes no options."
        raise OrderProblem(staff_next(f"'{said}' is not an option for {item['name']}.{choices} "
                                      "If the caller wants something the menu cannot hold, it goes to staff."))

    def price(self, item: dict, size: str | None, options: list[str]) -> float:
        base = item["sizes"][size] if size else item["price"]
        extras = sum(self.groups[group]["options"][option]["price"]
                     for group in item["groups"] for option in options if option in self.groups[group]["options"])
        return round(base + extras, 2)


@dataclass
class Line:
    item: str
    name: str
    size: str | None
    options: list[str]
    quantity: int
    each: float

    def spoken(self) -> str:
        """How the agent reads the line back: the same words a person at the counter would use."""
        parts = [str(self.quantity), self.size or "", self.name, *(f"with {option}" for option in self.options)]
        return " ".join(part for part in parts if part)


@dataclass
class Till:
    """One call's ticket. It is open until the order is placed or handed to staff, and then it is shut,
    so a model that keeps talking cannot change an order the kitchen already has."""

    menu: Menu = field(default_factory=Menu)
    lines: list[Line] = field(default_factory=list)
    status: str = "open"
    name: str = ""
    staff_reason: str = ""
    order_number: int | None = None

    def is_open(self) -> None:
        if self.status == "placed":
            raise OrderProblem(f"Order {self.order_number} is already in the kitchen. Tell the caller a team member can change it.")
        if self.status == "staff":
            raise OrderProblem("This call is already with staff. Tell the caller a team member will call back.")

    def add_item(self, item: str, size: str | None = None, options: list[str] | None = None, quantity: int = 1) -> Line:
        self.is_open()
        try:
            found, named = self.menu.find_item(item)
        except OrderProblem as refusal:
            if not size:
                raise
            # "Diet Coke" in a "2 liter" size: the size words are part of the item's name.
            try:
                found, named = self.menu.find_item(f"{size} {item}")
            except OrderProblem:
                raise refusal from None  # the refusal names what the model sent, not what the till tried
            size = None
        # The same option said twice ("buffalo wings, buffalo sauce") is one choice, not two.
        chosen = [option for option in dict.fromkeys(self.menu.find_option(found, option)
                                                      for option in [*named, *(options or [])]) if option]
        quantity = int(quantity)
        if not 1 <= quantity <= MOST_OF_ONE_ITEM:
            raise OrderProblem(staff_next(f"{quantity} of one item is more than a phone order takes."))
        for group_name in found["groups"]:
            group = self.menu.groups[group_name]
            picked = [option for chosen_group, option in chosen if chosen_group == group_name]
            if group["pick"] == "one" and len(picked) > 1:
                raise OrderProblem(f"{found['name']} takes one {group_name}, not {' and '.join(picked)}. Ask the caller which one.")
            if group["pick"] == "one" and not picked:
                if "default" not in group:
                    # Wings without a sauce are not an order yet; a person at the counter would ask too.
                    raise OrderProblem(f"{found['name']} needs a {group_name}. Ask the caller: {', '.join(group['options'])}.")
                chosen.append((group_name, group["default"]))
        size = self.menu.find_size(found, size)
        names = [option for _, option in chosen]
        line = Line(found["id"], found["name"], size, names, quantity, self.menu.price(found, size, names))
        self.lines.append(line)
        return line

    def remove_item(self, line_number: int) -> Line:
        self.is_open()
        if not 1 <= int(line_number) <= len(self.lines):
            raise OrderProblem(f"There is no line {line_number}. The ticket has {len(self.lines)} lines: {self.read_back()}")
        return self.lines.pop(int(line_number) - 1)

    def place_order(self, name: str, order_number: int) -> int:
        self.is_open()
        if not self.lines:
            raise OrderProblem("The ticket is empty. Ask the caller what they would like.")
        if not plain(name):
            raise OrderProblem("An order needs the caller's name for pickup. Ask for it.")
        self.status, self.name, self.order_number = "placed", name.strip().rstrip(".").title(), order_number
        return order_number

    def send_to_staff(self, reason: str) -> None:
        self.is_open()
        # Nothing reaches the kitchen: a half-keyed order is worse than a call back.
        self.status, self.staff_reason = "staff", reason.strip()

    def total(self) -> dict:
        subtotal = round(sum(line.each * line.quantity for line in self.lines), 2)
        tax = round(subtotal * self.menu.data["tax_rate"], 2)
        return {"subtotal": subtotal, "tax": tax, "total": round(subtotal + tax, 2)}

    def read_back(self) -> str:
        return "; ".join(f"line {number}: {line.spoken()}" for number, line in enumerate(self.lines, 1)) or "empty"

    def ticket(self) -> dict:
        return {"status": self.status, "name": self.name, "order_number": self.order_number,
                "staff_reason": self.staff_reason, **self.total(),
                "lines": [{"item": line.item, "name": line.name, "size": line.size, "options": line.options,
                           "quantity": line.quantity, "each": line.each} for line in self.lines]}


def counted(lines: list[dict]) -> Counter:
    """Two "1 small cheese" lines and one "2 small cheese" line are the same order to the kitchen."""
    counts: Counter = Counter()
    for line in lines:
        counts[(line["item"], line["size"], tuple(sorted(line["options"])))] += line["quantity"]
    return counts


def describe(key: tuple, quantity: int) -> str:
    item, size, options = key
    return " ".join(part for part in [str(quantity), size or "", item, *(f"+{option}" for option in options)] if part)


def check(ticket: dict, keyed: dict) -> tuple[bool, str]:
    """Compare the agent's ticket with what a person keyed from the same call. Only an exact match counts
    as an order taken with no staff: a wrong sauce is a remade order and an unhappy caller."""
    if "staff" in keyed:
        if ticket["status"] == "staff":
            return True, f"sent to staff: {ticket['staff_reason']}"
        return False, f"should have gone to staff ({keyed['staff']}), but the ticket is {ticket['status']}"
    if ticket["status"] != "placed":
        return False, f"not placed: {ticket['status']}" + (f" ({ticket['staff_reason']})" if ticket["staff_reason"] else "")
    taken, wanted = counted(ticket["lines"]), counted(keyed["lines"])
    missing, extra = wanted - taken, taken - wanted
    if missing or extra:
        return False, "; ".join([*(f"missing {describe(key, count)}" for key, count in missing.items()),
                                 *(f"extra {describe(key, count)}" for key, count in extra.items())])
    if plain(ticket["name"]) != plain(keyed["name"]):
        return False, f"name {ticket['name']!r}, keyed as {keyed['name']!r}"
    return True, "matches the keyed order"

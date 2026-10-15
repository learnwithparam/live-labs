"""Northfield's booking system: the truth the phone agent must change, outside the model.

Each call works on its own copy of `data/schedule.json`, so ten calls in a row start from the same
week and the review can diff what each call really changed. The agent only touches it through these
methods, and every method refuses what the office would refuse: a full window, a visit that is not the
caller's, an address outside the service area."""

import json
from pathlib import Path

from northfield.config import DATA

KINDS = ("tune-up", "repair", "estimate")
REASONS = ("safety", "billing", "caller_asked", "other")


def original() -> dict:
    return json.loads((DATA / "schedule.json").read_text())


class Schedule:
    def __init__(self, path: Path):
        # A fresh copy of the week for this call, unless the call already started one.
        self.path = path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(original(), indent=1))
        self.book_of_record = json.loads(path.read_text())

    def save(self) -> None:
        self.path.write_text(json.dumps(self.book_of_record, indent=1))

    def customer(self, phone: str) -> dict | None:
        return next((person for person in self.book_of_record["customers"] if person["phone"] == phone), None)

    def window(self, window_id: str) -> dict | None:
        return next((slot for slot in self.book_of_record["windows"] if slot["id"] == window_id), None)

    def visits_of(self, phone: str) -> list[dict]:
        return [visit for visit in self.book_of_record["visits"] if visit["phone"] == phone and visit["status"] == "booked"]

    def caller_card(self, phone: str) -> str:
        """What the agent knows before it says hello: caller ID matched to the customer list."""
        person = self.customer(phone)
        if not person:
            return f"Caller ID {phone} is not a customer yet. Ask for their name, street address and zip before booking."
        visits = "; ".join(f"{visit['id']} {visit['kind']} in {self.label(visit['window'])}" for visit in self.visits_of(phone))
        member = "a Comfort Club member" if person["member"] else "not a Comfort Club member"
        # The address stays out: caller ID can be faked, and the agent must never read a home address to a stranger.
        return (f"Caller ID {phone} is {person['name']}, {member}, equipment {person['equipment']}. "
                f"Booked visits: {visits or 'none'}. To move or cancel one, the caller must give the house number "
                f"of the address on file.")

    def label(self, window_id: str) -> str:
        slot = self.window(window_id)
        return f"{slot['day']}, {slot['time']}" if slot else window_id

    def open_windows(self) -> list[dict]:
        return [slot for slot in self.book_of_record["windows"] if slot["spots"] > 0]

    def take_spot(self, window_id: str) -> str | None:
        """A refusal sentence, or None when a spot was taken. The agent reads the refusal to the caller."""
        slot = self.window(window_id)
        if not slot:
            return f"There is no window {window_id}. Use an id from find_windows."
        if slot["spots"] <= 0:
            return f"{self.label(window_id)} is full. Offer one of the open windows instead."
        slot["spots"] -= 1
        return None

    def book(self, phone: str, kind: str, window_id: str, problem: str,
             name: str = "", address: str = "", zip_code: str = "") -> dict:
        if kind not in KINDS:
            return {"ok": False, "reason": f"Kind must be one of {', '.join(KINDS)}."}
        # Speech to text writes a zip the way it writes any number, "43,123"; the book wants five digits.
        zip_code = "".join(character for character in zip_code if character.isdigit())
        person = self.customer(phone)
        if not person:
            # A new caller becomes a customer only with a full address inside the area the vans cover.
            if not (name and address and zip_code):
                return {"ok": False, "reason": "A new caller needs a name, street address and zip first."}
            if zip_code not in self.book_of_record["service_zips"]:
                return {"ok": False, "reason": f"Zip {zip_code} is outside the service area. Decline politely."}
        refusal = self.take_spot(window_id)
        if refusal:
            return {"ok": False, "reason": refusal}
        if not person:
            self.book_of_record["customers"].append({"phone": phone, "name": name, "address": address, "zip": zip_code,
                                                     "equipment": "unknown", "member": False})
        visit_id = f"V-{max(int(visit['id'][2:]) for visit in self.book_of_record['visits']) + 1}"
        self.book_of_record["visits"].append({"id": visit_id, "phone": phone, "kind": kind, "window": window_id,
                                              "problem": problem, "status": "booked"})
        self.save()
        return {"ok": True, "visit": visit_id, "when": self.label(window_id)}

    def owned_visit(self, phone: str, visit_id: str) -> dict | None:
        return next((visit for visit in self.visits_of(phone) if visit["id"] == visit_id), None)

    def verified(self, phone: str, house_number: str) -> bool:
        """Caller ID can be spoofed, so changing a visit also needs the house number on file."""
        person = self.customer(phone)
        # Speech to text may write "1,207" or "12 07"; only the digits are compared.
        digits = "".join(character for character in house_number if character.isdigit())
        return bool(person and digits and person["address"].split()[0] == digits)

    def changeable_visit(self, phone: str, visit_id: str, house_number: str) -> tuple[dict | None, dict | None]:
        """The caller's visit, or the refusal the agent reads back. Ownership first, then the house number."""
        visit = self.owned_visit(phone, visit_id)
        if not visit:
            return None, {"ok": False, "reason": f"{visit_id} is not a booked visit for this caller."}
        if not self.verified(phone, house_number):
            return None, {"ok": False, "reason": "That house number does not match the address on file. Ask the caller "
                                                 "for it again, and do not say the address yourself."}
        return visit, None

    def move(self, phone: str, visit_id: str, window_id: str, house_number: str) -> dict:
        visit, refusal = self.changeable_visit(phone, visit_id, house_number)
        if refusal:
            return refusal
        refusal = self.take_spot(window_id)
        if refusal:
            return {"ok": False, "reason": refusal}
        # The old window gets its spot back, so another caller can have it.
        self.window(visit["window"])["spots"] += 1
        visit["window"] = window_id
        self.save()
        return {"ok": True, "visit": visit_id, "when": self.label(window_id)}

    def cancel(self, phone: str, visit_id: str, house_number: str) -> dict:
        visit, refusal = self.changeable_visit(phone, visit_id, house_number)
        if refusal:
            return refusal
        visit["status"] = "cancelled"
        self.window(visit["window"])["spots"] += 1
        self.save()
        return {"ok": True, "visit": visit_id}

    def hand_off(self, phone: str, reason: str, summary: str) -> dict:
        """The call goes to a person in the office, with the reason and what they need to know."""
        if reason not in REASONS:
            return {"ok": False, "reason": f"Reason must be one of {', '.join(REASONS)}."}
        self.book_of_record["handoffs"].append({"phone": phone, "reason": reason, "summary": summary})
        self.save()
        return {"ok": True, "reason": reason}


def changes(before: dict, after: dict) -> dict:
    """What one call changed in the book: new or altered visits, and handoffs. The review reads only this."""
    old_visits = {visit["id"]: visit for visit in before["visits"]}
    visits = [visit for visit in after["visits"] if old_visits.get(visit["id"]) != visit]
    new_customers = [person for person in after["customers"] if person not in before["customers"]]
    return {"visits": visits, "customers": new_customers, "handoffs": after["handoffs"][len(before["handoffs"]):]}

"""Halyard Freight's books: the road contract, September's carrier invoices, and what each should have billed.

The contract is the system of record. `owed` prices a shipment from the contract and the delivery record
alone, the way the accounts payable team does it by hand, so the agent's audit is scored against
arithmetic the model never wrote.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
# Carriers round to the cent, so an invoice within half a euro of the contract is paid as billed.
TOLERANCE = 0.50
SERVICES = {"tail_lift": "tail-lift delivery", "adr": "dangerous goods (ADR)"}


@dataclass(frozen=True)
class Finding:
    invoice: str
    verdict: str  # "approve" or "dispute"
    overcharge: float
    reason: str


def contract() -> dict:
    return json.loads((DATA / "contract.json").read_text())


def invoices() -> list[dict]:
    return json.loads((DATA / "invoices.json").read_text())


def fuel_week(pickup: str) -> str:
    """The contract's fuel index runs Monday to Sunday by ISO week, keyed on the pickup date."""
    year, week, _ = date.fromisoformat(pickup).isocalendar()
    return f"{year}-W{week:02d}"


def week_dates(week: str) -> str:
    """A fuel week as the contract prints it, Monday to Sunday, so nobody reckons ISO weeks by hand."""
    year, number = week.split("-W")
    monday, sunday = date.fromisocalendar(int(year), int(number), 1), date.fromisocalendar(int(year), int(number), 7)
    return f"{monday:%d %b} to {sunday:%d %b}"


def billed(invoice: dict) -> float:
    return round(sum(line["amount"] for line in invoice["lines"]), 2)


def contract_lines(invoice: dict, terms: dict) -> list[tuple[str, float]]:
    """Halyard's rate engine: what the contract says this shipment costs, one line per clause."""
    rate = next(lane["per_pallet"] for lane in terms["lanes"]
                if lane["carrier"] == invoice["carrier"] and lane["destination"] == invoice["destination"])
    linehaul = round(invoice["pallets"] * rate, 2)
    week = fuel_week(invoice["pickup"])
    lines = [(f"Linehaul Rotterdam to {invoice['destination']}, {invoice['pallets']} pallets at {rate:.2f}", linehaul),
             (f"Fuel surcharge {terms['fuel'][week] * 100:.1f}% (pickup week {week})", round(linehaul * terms["fuel"][week], 2))]
    delivered, booked = invoice["delivered"], invoice["booked"]
    # A service the carrier performed but nobody ordered is the carrier's cost, not ours.
    if "tail_lift" in booked and delivered["tail_lift_stops"]:
        lines.append((f"Tail-lift delivery, {delivered['tail_lift_stops']} stop(s) at {terms['tail_lift']:.2f}",
                      delivered["tail_lift_stops"] * terms["tail_lift"]))
    if "adr" in booked and delivered["dangerous_goods"]:
        lines.append((f"Dangerous goods (ADR) {terms['adr_share'] * 100:.0f}% of linehaul", round(linehaul * terms["adr_share"], 2)))
    paid_hours = max(delivered["hours_on_site"] - terms["waiting_free_hours"], 0)
    if paid_hours:
        lines.append((f"Waiting time, {paid_hours} h after {terms['waiting_free_hours']} free at {terms['waiting_per_hour']:.2f}",
                      paid_hours * terms["waiting_per_hour"]))
    return lines


def owed(invoice: dict, terms: dict) -> float:
    return round(sum(amount for _, amount in contract_lines(invoice, terms)), 2)


def expected() -> dict[str, Finding]:
    """The finding each invoice should get, in invoice order."""
    terms, first_bill, found = contract(), {}, {}
    for invoice in invoices():
        invoice_id, total = invoice["id"], billed(invoice)
        if invoice["shipment"] in first_bill:
            # A second bill for one shipment is disputed in full, however right its arithmetic.
            found[invoice_id] = Finding(invoice_id, "dispute", total, f"duplicate of {first_bill[invoice['shipment']]}")
            continue
        first_bill[invoice["shipment"]] = invoice_id
        overcharge = round(total - owed(invoice, terms), 2)
        found[invoice_id] = (Finding(invoice_id, "dispute", overcharge, "bills more than the contract") if overcharge > TOLERANCE
                       else Finding(invoice_id, "approve", 0.0, "matches the contract"))
    return found


def contract_text(terms: dict) -> str:
    """The contract as the agent reads it: the same text on every step, so it caches."""
    lanes = "\n".join(f"{lane['carrier']}  Rotterdam to {lane['destination']:<14}{lane['per_pallet']:>8.2f} per pallet"
                      for lane in terms["lanes"])
    weeks = [f"{week}  {week_dates(week)}  {share * 100:.1f}%" for week, share in terms["fuel"].items()]
    carriers = "\n".join(f"{code}  {carrier['name']}, {carrier['address']}, VAT {carrier['vat']}, payment {carrier['terms']} days"
                         for code, carrier in terms["carriers"].items())
    return f"""ROAD FREIGHT CONTRACT {terms['contract']}, valid {terms['valid']}. Customer: {terms['client']}.
All amounts in {terms['currency']}, VAT reverse charged.

Carriers
{carriers}

1. Linehaul. The carrier bills pallets times the lane rate below. A lane not listed is not covered.
2. Fuel surcharge. A share of the linehaul, from the fuel index of the week the shipment was picked up.
   Weeks run Monday to Sunday. The invoice date does not set the week.
{chr(10).join('   ' + line for line in weeks)}
3. Tail-lift delivery. {terms['tail_lift']:.2f} per stop, payable only when tail-lift was booked on the order.
4. Waiting time. The first {terms['waiting_free_hours']} hours on site are free. Each further hour is {terms['waiting_per_hour']:.2f}.
5. Dangerous goods (ADR). {terms['adr_share'] * 100:.0f}% of the linehaul, payable only when ADR was booked on the order.
6. One invoice per shipment. A second invoice for a shipment already billed is not payable.
7. Disputes. The customer may withhold the disputed amount and pays the rest on the due date.

Lane rates
{lanes}
"""


def invoice_text(invoice: dict, terms: dict) -> str:
    """The invoice as the carrier's PDF reads once extracted to text."""
    carrier = terms["carriers"][invoice["carrier"]]
    charges = "\n".join(f"  {line['text']:<66}{line['amount']:>10.2f}" for line in invoice["lines"])
    booked = ", ".join(SERVICES[service] for service in invoice["booked"]) or "none"
    delivered = invoice["delivered"]
    return f"""{carrier['name']}
{carrier['address']}   VAT {carrier['vat']}

INVOICE {invoice['number']}   Date {invoice['date']}   Due in {carrier['terms']} days
Bill to: {terms['client']}
Contract: {terms['contract']}
Shipment {invoice['shipment']}   {invoice['waybill']}   Picked up {invoice['pickup']} at Rotterdam
Deliver to: {invoice['consignee']}, {invoice['destination']}
Booked on the order: {booked}
Delivery record: {delivered['tail_lift_stops']} tail-lift stop(s), {delivered['hours_on_site']} h on site, dangerous goods {'yes' if delivered['dangerous_goods'] else 'no'}

Charges (EUR)
{charges}
  {'Total due':<66}{billed(invoice):>10.2f}

VAT reverse charged under Article 196 of Directive 2006/112/EC.
Pay to IBAN {carrier['iban']}, reference {invoice['number']}.
Late payment carries interest at 9 points above the ECB rate under Directive 2011/7/EU.
"""


def rated_text(invoice: dict, terms: dict) -> str:
    """The invoice beside the rate engine's price, the way an accounts payable screen shows them.

    Pricing stays in code. Asked to price shipments themselves, models misread the fuel table and paid
    unbooked services on most runs; comparing two sets of lines and naming the wrong one, they do not.
    """
    rated = "\n".join(f"  {text:<66}{amount:>10.2f}" for text, amount in contract_lines(invoice, terms))
    return (invoice_text(invoice, terms) + f"\nRate engine price under {terms['contract']} (EUR)\n{rated}\n"
            f"  {'Contract total':<66}{owed(invoice, terms):>10.2f}\n")

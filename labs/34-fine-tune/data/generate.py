"""How `reviewed.jsonl` and `incoming.jsonl` were made. Run it to rebuild them byte for byte.

No real card data can ship in a public lab, so the reviewed labels are synthetic, built to behave like
Corvane's review queue export. Three things make them realistic rather than easy:

- Descriptors look like card network strings: processor prefixes (SQ *, TST*, PAYPAL *), store numbers,
  phone numbers, city and state, cut at 25 characters the way networks cut them.
- Labels follow Corvane's written policy and also its unwritten habits (Mailchimp is software, Costco is
  meals), which no prompt states. That is the knowledge only the reviewed labels carry.
- Some merchants are genuinely ambiguous from the descriptor alone (Expedia is a flight or a hotel,
  FedEx Office is printing or shipping). The reviewer saw the receipt; a model cannot, so nothing scores 100.
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
CITIES = ["SAN FRANCISCO CA", "NEW YORK NY", "AUSTIN TX", "CHICAGO IL", "SEATTLE WA", "DENVER CO",
          "BOSTON MA", "ATLANTA GA", "LOS ANGELES CA", "BROOKLYN NY", "PORTLAND OR", "MIAMI FL", "OAKLAND CA"]
REVIEWERS = ["dana.okafor", "luis.mendez", "priya.raman", "tom.becker"]

# One merchant: descriptor templates, the label (or label odds when the descriptor alone cannot decide),
# a weight for how often it shows up on cards, and a typical amount range in dollars.
MERCHANTS = [
    # Airfare, including fees an airline charges and the tacit habit that CLEAR counts as travel.
    (["UNITED {n13}", "UNITED AIRLINES {n13}", "UNITED     {n10} HOUSTON TX"], "airfare", 9, (89, 780)),
    (["DELTA AIR {n13}", "DELTA AIR LINES ATLANTA", "DELTA AIR {n10} DELTA.COM GA"], "airfare", 9, (99, 820)),
    (["SOUTHWES {n13}", "SOUTHWEST AIRLINES DALLAS TX"], "airfare", 6, (59, 420)),
    (["ALASKA AIR {n13}", "JETBLUE {n13}", "AMERICAN AIR{n13}"], "airfare", 6, (79, 690)),
    (["DELTA AIR BAG FEE ATLANTA GA", "UNITED BAG FEE {n6}", "AMERICAN AIR BAGGAGE FEE"], "airfare", 3, (35, 70)),
    (["CLEAR *CLEARME.COM NY", "CLEAR SECURE INC {n6}"], "airfare", 2, (189, 189)),
    # Lodging, plus the hotel bar and restaurant charges that policy moves to meals below.
    (["MARRIOTT {city}", "MARRIOTT {n5} {city}", "COURTYARD BY MARRIOTT {st}"], "lodging", 7, (149, 620)),
    (["HILTON GARDEN INN {city}", "HAMPTON INN {n4} {st}", "HILTON HOTELS {city}"], "lodging", 7, (129, 540)),
    (["HYATT REGENCY {city}", "HYATT PLACE {n4} {st}"], "lodging", 4, (159, 610)),
    (["AIRBNB * HM{ref}", "AIRBNB  HM{ref} SAN FRANCISCO"], "lodging", 6, (180, 1400)),
    (["BOOKING.COM HOTEL AMSTERDAM", "HOTEL AT BOOKING.COM {n6}"], "lodging", 3, (120, 700)),
    (["THE {word} HOTEL {city}", "{word} INN & SUITES {st}"], "lodging", 4, (110, 480)),
    (["MARRIOTT {word} BAR {st}", "HILTON {word} GRILL {city}", "HYATT LOBBY BAR {city}"], "meals", 3, (18, 140)),
    # Ground transport: rides, trains, parking, tolls, fuel and car rental, but never food from a ride app.
    (["UBER   *TRIP HELP.UBER.COM CA", "UBER *TRIP {ref}", "UBER* TRIP"], "ground_transport", 11, (9, 85)),
    (["LYFT   *RIDE {weekday} {n2}PM", "LYFT *RIDE SAN FRANCISCO"], "ground_transport", 6, (8, 70)),
    (["AMTRAK .COM {n10}", "AMTRAK {n13} WASHINGTON DC"], "ground_transport", 3, (29, 240)),
    (["SQ *{word} PARKING {st}", "SP+ PARKING {n4} {city}", "PARKWHIZ {ref}", "SFO PARKING SAN FRANCISCO"], "ground_transport", 5, (12, 96)),
    (["SHELL OIL {n11}", "CHEVRON {n7} {city}", "EXXONMOBIL {n8}"], "ground_transport", 4, (32, 88)),
    (["HERTZ CAR RENTAL {city}", "ENTERPRISE RENT-A-CAR {n6}", "NATIONAL CAR RENTAL {st}"], "ground_transport", 3, (95, 640)),
    (["FASTRAK CSC {n6} CA", "E-ZPASS NY REBILL", "NTTA AUTOCHARGE TX"], "ground_transport", 2, (10, 40)),
    # Meals: restaurants behind Square and Toast, delivery apps, coffee and catering.
    (["UBER   *EATS HELP.UBER.COM CA", "UBER *EATS PENDING", "UBER* EATS {ref}"], "meals", 9, (14, 160)),
    (["DOORDASH*{word} {ref}", "DD *DOORDASH {word}", "GRUBHUB*{word}"], "meals", 8, (16, 180)),
    (["TST* {word} {food} {city}", "TST*{word} {food}"], "meals", 10, (22, 380)),
    (["SQ *{word} {food} {st}", "SQ *{word} COFFEE"], "meals", 9, (5, 120)),
    (["STARBUCKS STORE {n5}", "BLUE BOTTLE COFFEE {city}", "SWEETGREEN {word}", "CHIPOTLE {n4}"], "meals", 8, (5, 60)),
    (["EZCATER *{word} {food}", "EZCATER {ref}"], "meals", 3, (180, 1600)),
    (["COSTCO WHSE #{n4} {city}", "COSTCO WHOLESALE {n4}"], "meals", 2, (90, 520)),
    # Software, including cloud and API spend, and the unwritten habit that email tools are software.
    (["GITHUB, INC. GITHUB.COM CA", "GITHUB {ref}"], "software", 5, (4, 420)),
    (["ATLASSIAN {ref}", "SLACK T{ref} DUBLIN", "NOTION LABS, INC.", "FIGMA {ref}"], "software", 9, (8, 900)),
    (["AMAZON WEB SERVICES AWS.AMAZON.CO WA", "AWS EMEA aws.amazon.com"], "software", 6, (40, 9800)),
    (["GOOGLE *CLOUD {ref}", "GOOGLE *GSUITE_{word}", "GOOGLE *WORKSPACE {ref}"], "software", 6, (12, 3400)),
    (["OPENAI *CHATGPT SUBSCR", "OPENAI SAN FRANCISCO CA", "ANTHROPIC SAN FRANCISCO CA"], "software", 5, (20, 2400)),
    (["ZOOM.US 888-799-9666 CA", "DROPBOX*{ref}", "ADOBE  *ACROPRO SUBS", "CANVA* {ref}"], "software", 7, (12, 600)),
    (["TWILIO SENDGRID", "TWILIO INC {ref}", "MAILCHIMP *MONTHLY", "INTUIT *MAILCHIMP"], "software", 4, (20, 1100)),
    (["APPLE.COM/BILL 866-712-7753 CA", "APPLE.COM/US {n6}"], "software", 3, (1, 50)),
    (["GUSTO {ref}", "LINKEDIN PREMIUM {ref}"], "software", 2, (39, 280)),
    # Advertising: paid media on any platform, plus printed marketing.
    (["GOOGLE *ADS{n10}", "GOOGLE ADS {n10} CC@GOOGLE.COM"], "advertising", 6, (100, 6000)),
    (["FACEBK *{ref}", "META PLATFORMS *ADS {ref}", "FACEBK ADS {n10}"], "advertising", 5, (50, 4800)),
    (["LINKEDIN ADS {n9}", "REDDIT ADS {ref}", "X CORP. PAID FEATURES"], "advertising", 3, (100, 3000)),
    (["VISTAPRINT.COM {n6}", "MOO PRINT LTD {ref}"], "advertising", 2, (30, 650)),
    # Office supplies: stores, marketplaces, hardware and furniture.
    (["STAPLES {n5} {city}", "OFFICE DEPOT #{n4}", "STAPLES.COM {n6}"], "office_supplies", 5, (8, 420)),
    (["AMZN MKTP US*{ref}", "AMAZON.COM*{ref} AMZN.COM/BILL", "AMZN Mktp US*{ref}"], "office_supplies", 9, (9, 640)),
    (["APPLE STORE R{n3} {city}", "BEST BUY {n8}", "B&H PHOTO 800-606-6969"], "office_supplies", 4, (29, 3200)),
    (["IKEA {city}", "UPLIFT DESK {ref}", "HERMAN MILLER STORE {st}"], "office_supplies", 2, (60, 1900)),
    # Shipping and postage.
    (["USPS PO {n10} {city}", "USPS.COM CLICKNSHIP"], "shipping", 4, (6, 140)),
    (["UPS*{ref}", "THE UPS STORE {n4}", "FEDEX {n9} MEMPHIS TN"], "shipping", 5, (11, 420)),
    (["SHIPPO *{ref}", "STAMPS.COM {n6}", "DHL EXPRESS USA {ref}"], "shipping", 3, (9, 380)),
    # Telecom: phone, internet, business phone lines and inflight wifi.
    (["VERIZON WRLS {n5}-01", "AT&T*BILL PAYMENT {st}", "T-MOBILE*AUTO PAY"], "telecom", 5, (45, 920)),
    (["COMCAST BUSINESS {n6}", "SPECTRUM {n4}", "SONIC.NET INC"], "telecom", 3, (79, 410)),
    (["GOGOAIR.COM {n6}", "VIASAT INFLIGHT WIFI", "UNITED WI-FI {n6}"], "telecom", 2, (8, 49)),
    (["RINGCENTRAL INC {ref}", "DIALPAD INC {ref}"], "telecom", 2, (30, 600)),
    # Professional services: contractors, lawyers, accountants and filings.
    (["UPWORK -{n9}REF", "FIVERR {ref}", "TOPTAL LLC {ref}"], "professional_services", 4, (60, 4800)),
    (["{surname} & {surname} LLP", "{surname} LAW GROUP {st}", "{surname} CPA PLLC"], "professional_services", 3, (250, 9000)),
    (["LEGALZOOM* {ref}", "STRIPE ATLAS", "CLERKY INC {ref}"], "professional_services", 2, (99, 900)),
    (["LINKEDIN JOBS {n9}", "LINKEDIN *JOB POSTING"], "professional_services", 1, (149, 899)),
    # Ambiguous from the descriptor alone. The reviewer read the receipt; a model only sees these odds.
    (["EXPEDIA {n13}", "EXPEDIA*{ref} EXPEDIA.COM WA"], {"airfare": 0.55, "lodging": 0.45}, 4, (110, 1300)),
    (["FEDEX OFFIC{n5} {city}", "FEDEX OFFICE {n4}"], {"office_supplies": 0.6, "shipping": 0.4}, 3, (6, 260)),
    (["PAYPAL *{word}STUDIO", "PAYPAL *{word}MEDIA {n10}"], {"professional_services": 0.5, "advertising": 0.3, "software": 0.2}, 3, (40, 2200)),
    (["AMAZON.COM SERVICES {ref}", "AMZN DIGITAL*{ref}"], {"software": 0.55, "office_supplies": 0.45}, 2, (3, 260)),
    (["SQ *{word} EVENTS", "EVENTBRITE *{word}"], {"advertising": 0.5, "meals": 0.3, "professional_services": 0.2}, 2, (25, 2500)),
]

WORDS = ["BLUE OAK", "NOMAD", "LUPA", "SAFFRON", "NORTHSIDE", "HARBOR", "MERIDIAN", "PEARL", "JUNIPER", "COPPER",
         "TALLOW", "BRASS", "FERN", "MAPLE", "KESTREL", "ORCHARD", "LANTERN", "CEDAR", "MARIGOLD", "WILLOW"]
FOODS = ["TRATTORIA", "TAQUERIA", "RAMEN", "BISTRO", "KITCHEN", "BAKERY", "DELI", "BBQ", "SUSHI", "CAFE", "PIZZA"]
SURNAMES = ["HOLLOWAY", "ABARA", "CHEN", "MORALES", "FINCH", "OKONKWO", "BRANDT", "SATO", "RIVERA", "LOWELL"]
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def fill(template: str, chance: random.Random) -> str:
    """Fill one template, then cut it the way the card network does: 25 characters of name, then city."""
    def digits(count: int) -> str:
        return "".join(chance.choice("0123456789") for _ in range(count))
    city = chance.choice(CITIES)
    values = {"city": city, "st": city[-2:], "word": chance.choice(WORDS), "food": chance.choice(FOODS),
              "surname": chance.choice(SURNAMES), "weekday": chance.choice(WEEKDAYS),
              "ref": "".join(chance.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(chance.randint(6, 9)))}
    values.update({f"n{count}": digits(count) for count in range(2, 14)})
    # Templates repeat a key like {surname} twice; each occurrence draws on its own.
    text = template
    while "{surname}" in text:
        text = text.replace("{surname}", chance.choice(SURNAMES), 1)
    text = text.format(**values)
    return text[:40].rstrip()


def draw(chance: random.Random) -> tuple[str, str, float]:
    templates, label, _, (low, high) = chance.choices(MERCHANTS, weights=[row[2] for row in MERCHANTS])[0]
    if isinstance(label, dict):
        label = chance.choices(list(label), weights=list(label.values()))[0]
    amount = round(chance.uniform(low, high), 2) if high > low else float(low)
    return fill(chance.choice(templates), chance), label, amount


def build(count: int, seed: int, prefix: str, start: datetime, labelled: bool) -> list[dict]:
    chance = random.Random(seed)
    all_labels = sorted({row[1] for row in MERCHANTS if isinstance(row[1], str)})
    rows = []
    for number in range(1, count + 1):
        descriptor, label, amount = draw(chance)
        row = {"id": f"{prefix}-{number:05d}", "descriptor": descriptor, "amount": amount,
               "posted": (start + timedelta(minutes=chance.randint(0, 60 * 24 * 56))).strftime("%Y-%m-%d")}
        if labelled:
            # About one review in fifty is a slip: a tired reviewer picks a neighbour. Real queues have them.
            if chance.random() < 0.02:
                label = chance.choice([other for other in all_labels if other != label])
            row.update(label=label, reviewer=chance.choice(REVIEWERS))
        rows.append(row)
    return sorted(rows, key=lambda row: (row["posted"], row["id"]))


def main() -> None:
    reviewed = build(1200, seed=34, prefix="TX", start=datetime(2026, 7, 6), labelled=True)
    incoming = build(150, seed=3434, prefix="NX", start=datetime(2026, 9, 28), labelled=False)
    (HERE / "reviewed.jsonl").write_text("".join(json.dumps(row) + "\n" for row in reviewed))
    (HERE / "incoming.jsonl").write_text("".join(json.dumps(row) + "\n" for row in incoming))
    print(f"{len(reviewed)} reviewed labels, {len(incoming)} incoming items")


if __name__ == "__main__":
    main()

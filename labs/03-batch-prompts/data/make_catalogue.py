"""Write the synthetic grocery catalogue (`catalogue.csv`) and the attributes retailers supplied for a
sample of it (`retailer-attributes.csv`). Seeded, so a rerun writes the same files.

    python3 data/make_catalogue.py
"""

import csv
import random
from pathlib import Path

HERE = Path(__file__).parent
# category, title template, size choices, vegan, organic possible, ingredients line or "" when unknown
KINDS = [
    ("produce", "{brand} {organic}Bananas", [("count", 6), ("count", 5)], "yes", True, "Bananas."),
    ("produce", "{brand} {organic}Baby Spinach", [("g", 142), ("g", 312)], "yes", True, "Spinach."),
    ("produce", "{brand} {organic}Gala Apples Bag", [("g", 1360), ("g", 907)], "yes", True, "Apples."),
    ("dairy", "{brand} {organic}Whole Milk", [("ml", 1890), ("ml", 3780)], "no", True, "Grade A milk, vitamin D3."),
    ("dairy", "{brand} Greek Yogurt Plain", [("g", 907), ("g", 454)], "no", True, "Cultured pasteurized milk."),
    ("dairy", "{brand} Sharp Cheddar Block", [("g", 227), ("g", 454)], "no", False, "Milk, salt, cultures, enzymes."),
    ("bakery", "{brand} Sourdough Loaf", [("g", 680)], "yes", True, "Wheat flour, water, salt, starter."),
    ("bakery", "{brand} Butter Croissants", [("count", 4), ("count", 6)], "no", False, "Wheat flour, butter, egg, sugar, yeast."),
    ("meat_seafood", "{brand} Chicken Breast Fillets", [("g", 680), ("g", 1130)], "no", True, "Chicken."),
    ("meat_seafood", "{brand} Atlantic Salmon Portions", [("g", 340)], "no", False, "Atlantic salmon."),
    ("pantry", "{brand} {organic}Rolled Oats", [("g", 907), ("g", 1130)], "yes", True, "Whole grain rolled oats."),
    ("pantry", "{brand} {organic}Penne Rigate", [("g", 454)], "yes", True, "Durum wheat semolina."),
    ("pantry", "{brand} Creamy Peanut Butter", [("g", 454), ("g", 794)], "yes", True, "Peanuts, salt."),
    ("pantry", "{brand} Chicken Stock", [("ml", 946)], "no", True, "Chicken stock, salt, onion."),
    ("snacks", "{brand} Sea Salt Tortilla Chips", [("g", 283), ("g", 368)], "yes", True, "Corn, sunflower oil, sea salt."),
    ("snacks", "{brand} Milk Chocolate Bar", [("g", 100)], "no", True, "Sugar, cocoa butter, whole milk powder, cocoa mass."),
    ("snacks", "{brand} Assorted Biscuits Tin", [("g", 400)], "unknown", False, ""),
    ("beverages", "{brand} Sparkling Water Lime", [("ml", 355), ("ml", 1000)], "yes", False, "Carbonated water, natural lime flavor."),
    ("beverages", "{brand} {organic}Oat Milk Barista", [("ml", 946)], "yes", True, "Oat base (water, oats), rapeseed oil, salt."),
    ("beverages", "{brand} Cold Brew Coffee", [("ml", 1420)], "yes", True, "Water, coffee."),
    ("frozen", "{brand} Margherita Pizza", [("g", 340)], "no", False, "Wheat flour, mozzarella cheese, tomato, basil."),
    ("frozen", "{brand} Garden Vegetable Medley", [("g", 454)], "yes", True, "Broccoli, carrots, green beans."),
    ("frozen", "{brand} Chef's Party Platter", [("count", 24)], "unknown", False, ""),
    ("household", "{brand} Dish Soap Lemon", [("ml", 709)], "not_food", False, "Water, surfactants, fragrance."),
    ("household", "{brand} Paper Towels", [("count", 6), ("count", 12)], "not_food", False, ""),
    ("personal_care", "{brand} Shampoo Argan Oil", [("ml", 400)], "not_food", False, ""),
    ("personal_care", "{brand} Toothpaste Mint", [("g", 113)], "not_food", False, "Sodium fluoride, hydrated silica, glycerin, mint."),
]
BRANDS = ["Good Field", "Harvest Lane", "Northstar", "Pantry Co.", "Meadow & Main", "Simple Basket", "Bright Farms", "Old Mill"]
UNIT_WORDS = {"g": ["{v} g", "{v}g", "{oz} oz"], "ml": ["{v} ml", "{v}mL", "{floz} fl oz"], "count": ["{v} ct", "pack of {v}", "{v} count"]}


def size_text(unit: str, value: int, rng: random.Random) -> str:
    pattern = rng.choice(UNIT_WORDS[unit])
    return pattern.format(v=value, oz=round(value / 28.35, 1), floz=round(value / 29.57, 1))


def main() -> None:
    rng = random.Random(7)
    products, truth = [], []
    for n in range(240):
        category, title, sizes, vegan, can_be_organic, ingredients = KINDS[n % len(KINDS)]
        unit, value = rng.choice(sizes)
        organic = can_be_organic and rng.random() < 0.35
        brand = rng.choice(BRANDS)
        name = title.format(brand=brand, organic="Organic " if organic else "")
        listed = size_text(unit, value, rng)
        # Retailer feeds are messy: the size is in the title or only in the description, never in a field.
        if rng.random() < 0.5:
            name, description = f"{name}, {listed}", ""
        else:
            description = f"Net {listed}."
        if ingredients:
            description = f"{description} Ingredients: {ingredients}".strip()
        if organic:
            description = f"{description} USDA Organic.".strip()
        sku = f"SKU{10000 + n}"
        products.append({"sku": sku, "title": name, "description": description})
        # The retailer supplied attributes for one product in five, and leaves vegan blank when it does not know.
        if n % 5 == 0:
            truth.append({"sku": sku, "category": category, "size_value": value, "size_unit": unit,
                          "organic": str(organic).lower(), "vegan": "" if vegan == "unknown" else vegan})
    with (HERE / "catalogue.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["sku", "title", "description"])
        writer.writeheader()
        writer.writerows(products)
    with (HERE / "retailer-attributes.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(truth[0]))
        writer.writeheader()
        writer.writerows(truth)
    print(f"{len(products)} products, {len(truth)} with retailer attributes")


if __name__ == "__main__":
    main()

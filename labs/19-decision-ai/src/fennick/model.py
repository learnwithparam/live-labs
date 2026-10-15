"""The decision model: a gradient-boosted tree ensemble trained on past refunds, then calibrated.

Gradient-boosted trees are what payment risk teams run on tabular signals: they learn interactions
("not received" means little until the carrier has a signature) without hand-built features. Stripe
Radar's core model is the same family. Calibration then turns the trees' scores into probabilities a
threshold can trust.
"""

import hashlib
import pickle
from datetime import date
from pathlib import Path

import numpy
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator

from fennick.history import CATEGORIES, REASONS, SCANS, is_fraud

MODEL_FILE = Path("out/model.pkl")
# Split by time, never at random: the model is trained on the past and judged on months it never saw,
# the way it runs in production. A random split would leak a fraud ring's later refunds into training.
TRAIN_UNTIL, CALIBRATE_UNTIL = date(2026, 4, 30), date(2026, 5, 31)
NUMERIC = ["amount", "account_age_days", "orders_lifetime", "refunds_30d", "days_since_delivery"]
FLAGS = ["return_scanned", "address_changed_7d", "card_country_match"]
CATEGORICAL = {"category": CATEGORIES, "reason": REASONS, "delivery_scan": SCANS}
FEATURES = NUMERIC + FLAGS + list(CATEGORICAL)
SEED_FOR_TREES = 0


def window(rows: list[dict], name: str) -> list[dict]:
    """The rows of one time window: train (Jan to Apr), calibrate (May) or test (Jun and Jul)."""
    def month_of(row: dict) -> str:
        day = date.fromisoformat(row["requested_at"])
        return "train" if day <= TRAIN_UNTIL else "calibrate" if day <= CALIBRATE_UNTIL else "test"
    return [row for row in rows if month_of(row) == name]


def to_number(value: str) -> float:
    # A blank means "does not apply" (no return scan on a parcel that never came). The trees take NaN
    # as its own branch, so "not asked" never reads as "no".
    if value == "":
        return numpy.nan
    return {"yes": 1.0, "no": 0.0}[value] if value in ("yes", "no") else float(value)


def matrix(rows: list[dict]) -> numpy.ndarray:
    """One row of numbers per refund. Categories become their index, marked categorical for the model."""
    table = [[to_number(row[name]) for name in NUMERIC + FLAGS] +
             [float(choices.index(row[name])) for name, choices in CATEGORICAL.items()] for row in rows]
    return numpy.array(table, dtype=float)


def labels(rows: list[dict]) -> numpy.ndarray:
    return numpy.array([is_fraud(row) for row in rows], dtype=int)


def train(rows: list[dict]) -> dict:
    """Fit on the train window, calibrate on the next month, and keep both models.

    `class_weight="balanced"` makes the rare fraud rows count as much as the honest ones, which helps
    the trees rank. It also inflates every score, so the raw output is not a probability. Isotonic
    calibration on a later month, frozen trees untouched, maps scores back to observed fraud rates.
    """
    train_rows, calibrate_rows = window(rows, "train"), window(rows, "calibrate")
    trees = HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=30, l2_regularization=1.0,
        class_weight="balanced", early_stopping=False, random_state=SEED_FOR_TREES,
        categorical_features=[name in CATEGORICAL for name in FEATURES])
    trees.fit(matrix(train_rows), labels(train_rows))
    calibrated = CalibratedClassifierCV(FrozenEstimator(trees), method="isotonic")
    calibrated.fit(matrix(calibrate_rows), labels(calibrate_rows))
    return {"raw": trees, "calibrated": calibrated, "trained_on": len(train_rows),
            "calibrated_on": len(calibrate_rows)}


def risk(models: dict, rows: list[dict], calibrated: bool = True) -> numpy.ndarray:
    """The chance each refund turns out to be fraud, from 0 to 1."""
    return models["calibrated" if calibrated else "raw"].predict_proba(matrix(rows))[:, 1]


def save(models: dict, path: Path = MODEL_FILE) -> str:
    """Write the models and return a short version id. Every decision row records it, so an auditor
    can tell which model refused which refund months later."""
    path.parent.mkdir(exist_ok=True)
    blob = pickle.dumps(models)
    path.write_bytes(blob)
    return hashlib.sha256(blob).hexdigest()[:10]


def load(path: Path = MODEL_FILE) -> tuple[dict, str]:
    """Read back a model this lab wrote. Pickle runs code on load, so only ever load your own file."""
    if not path.exists():
        raise SystemExit("No trained model. Run `uv run refunds train` first.")
    blob = path.read_bytes()
    return pickle.loads(blob), hashlib.sha256(blob).hexdigest()[:10]

"""Where the signed inputs live and the one number the review holds every failure mode to."""

from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
DATA = LAB / "data"
RFC = LAB / "rfc"
OUT = LAB / "out"
# A failure mode with fewer labelled cases than this has no sample to measure against.
MIN_CASES_PER_FAILURE_MODE = 3
PORT = 8360

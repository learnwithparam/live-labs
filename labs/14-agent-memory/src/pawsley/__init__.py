"""Pawsley pet food support with long-term memory."""

from pawsley.config import quiet_mem0

# Before any module imports Mem0: it reads its telemetry switch and home folder at import time.
quiet_mem0()

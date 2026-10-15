import sys
from pathlib import Path

# Mem0 reads its settings on import, so pawsley sets them before the fakes import Mem0.
import pawsley  # noqa: F401

# The scripted model and embedder live beside the tests.
sys.path.insert(0, str(Path(__file__).parent))

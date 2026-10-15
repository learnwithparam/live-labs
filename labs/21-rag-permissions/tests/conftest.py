import sys
from pathlib import Path

# The scripted model and the fixed access table live beside the tests.
sys.path.insert(0, str(Path(__file__).parent))

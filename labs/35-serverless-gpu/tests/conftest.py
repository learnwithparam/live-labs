import sys
from pathlib import Path

# The fake endpoint lives beside the tests, and modal_app.py at the lab's root.
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1]))

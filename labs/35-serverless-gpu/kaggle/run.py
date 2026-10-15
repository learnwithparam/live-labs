"""The Kaggle notebook: find the lab's code in the private dataset and run the GPU run with its guard."""

import glob
import os
import sys
import time
from pathlib import Path

# The guard counts from here, the first line that runs on the GPU machine.
STARTED = time.time()
package = Path(glob.glob("/kaggle/input/**/corvane_gpu/gpu_run.py", recursive=True)[0]).parent
sys.path.insert(0, str(package.parent))
# The stand-in and vLLM run as child processes, and they find the package the same way.
os.environ["PYTHONPATH"] = str(package.parent)
data = Path(glob.glob("/kaggle/input/**/golden.jsonl", recursive=True)[0]).parent

from corvane_gpu.gpu_run import run_on_gpu  # noqa: E402

results = run_on_gpu(data, Path("/kaggle/working"), STARTED)
print("kernel seconds:", results["kernel_seconds"], "guard hit:", results["guard_hit"])

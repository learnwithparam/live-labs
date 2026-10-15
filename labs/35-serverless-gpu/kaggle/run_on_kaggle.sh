#!/usr/bin/env bash
# Run the GPU run on one Kaggle T4: vLLM serving Qwen3 0.6B, two waves from zero replicas and back.
#
#   kaggle/run_on_kaggle.sh
#
# Uploads the lab's code and golden set as a PRIVATE dataset with a unique name, pushes a GPU notebook
# that refuses to run without a GPU, waits, and downloads results.json into data/gpu-run/. The dataset is
# deleted on exit, whatever happened. Rehearse first with `uv run gpu rehearse`.
set -euo pipefail

LAB=$(cd "$(dirname "$0")/.." && pwd)
. "$LAB/kaggle/kaggle_lib.sh"
OWNER=$(kaggle_owner)
DATASET=$(kaggle_unique corvane-gpu-ds)
NOTEBOOK=$(kaggle_unique corvane-gpu-serve)
WORK=$(mktemp -d)
trap 'kaggle_finish "$WORK"' EXIT

mkdir -p "$WORK/ds/src" "$WORK/ds/data" "$WORK/notebook"
rsync -a --exclude __pycache__ "$LAB/src/corvane_gpu" "$WORK/ds/src/"
cp "$LAB/data/golden.jsonl" "$LAB/data/examples.jsonl" "$WORK/ds/data/"
kaggle_dataset "$OWNER" "$DATASET" "$WORK/ds"
cp "$LAB/kaggle/run.py" "$WORK/notebook/run.py"
echo "notebook: $OWNER/$NOTEBOOK"
kaggle_kernel "$OWNER" "$NOTEBOOK" "$DATASET" "$WORK/notebook" "$LAB/data/gpu-run"

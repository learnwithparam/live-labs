#!/usr/bin/env bash
# Train the LoRA adapter on a free Kaggle GPU. `uv run tune train` calls this; you can too.
#
#   kaggle/train.sh <dataset dir> <output dir>
#
# The dataset dir holds train.jsonl, valid.jsonl and recipe.json. It goes up as a private dataset, a GPU
# kernel runs kaggle/train_lora.py on it, and the adapter and metrics come back into the output dir.
# The dataset is deleted on exit, whatever happened. The Kaggle token stays in ~/.kaggle.
#
# Bounds, overridable from the environment: KAGGLE_POLL_SECONDS (30), KAGGLE_MAX_WAIT_SECONDS (1500),
# KAGGLE_MAX_STATUS_FAILS (5), KAGGLE_READY_SECONDS (600), KAGGLE_KERNEL_SECONDS (1200).
set -euo pipefail

DATASET_DIR=$1
OUTPUT_DIR=$2
HERE=$(cd "$(dirname "$0")" && pwd)
CREATED=()

finish() {
  local dataset
  for dataset in ${CREATED[@]+"${CREATED[@]}"}; do kaggle datasets delete "$dataset" -y >/dev/null 2>&1 || true; done
  rm -rf "$WORK"
}

OWNER=$(kaggle config view | sed -n 's/^- username: //p')
[ -n "$OWNER" ] || { echo "kaggle is not logged in: put your token in ~/.kaggle first" >&2; exit 1; }
# A name no earlier run used, so two runs never collide on a dataset or kernel slug.
RUN_ID=$(date +%m%d%H%M%S)-$$
DATASET=corvane-labels-$RUN_ID
KERNEL=corvane-lora-$RUN_ID
WORK=$(mktemp -d)
trap finish EXIT

mkdir -p "$WORK/dataset" "$WORK/kernel" "$OUTPUT_DIR"
cp "$DATASET_DIR"/train.jsonl "$DATASET_DIR"/valid.jsonl "$DATASET_DIR"/recipe.json "$WORK/dataset/"
printf '{"title": "%s", "id": "%s/%s", "licenses": [{"name": "other"}]}\n' "$DATASET" "$OWNER" "$DATASET" \
  > "$WORK/dataset/dataset-metadata.json"
kaggle datasets create -p "$WORK/dataset" -q   # private by default
CREATED+=("$OWNER/$DATASET")

# Kaggle builds a dataset in the background. A kernel pushed before it is ready silently runs without it.
started=$(date +%s); fails=0
while true; do
  if status=$(kaggle datasets status "$OWNER/$DATASET" 2>&1); then fails=0; else fails=$((fails + 1)); fi
  [ "$(echo "$status" | tail -1)" = "ready" ] && break
  [ "$fails" -lt "${KAGGLE_MAX_STATUS_FAILS:-5}" ] || { echo "dataset status failed $fails times: $status" >&2; exit 1; }
  [ $(($(date +%s) - started)) -lt "${KAGGLE_READY_SECONDS:-600}" ] || { echo "dataset not ready in time" >&2; exit 1; }
  sleep "${KAGGLE_POLL_SECONDS:-30}"
done
echo "dataset $OWNER/$DATASET ready"

cp "$HERE/train_lora.py" "$WORK/kernel/"
printf '{"id": "%s/%s", "title": "%s", "code_file": "train_lora.py", "language": "python", "kernel_type": "script", "is_private": "true", "enable_gpu": "true", "enable_internet": "true", "dataset_sources": ["%s/%s"]}\n' \
  "$OWNER" "$KERNEL" "$KERNEL" "$OWNER" "$DATASET" > "$WORK/kernel/kernel-metadata.json"
kaggle kernels push -p "$WORK/kernel" -t "${KAGGLE_KERNEL_SECONDS:-1200}"
echo "kernel $OWNER/$KERNEL pushed"

# Poll until the kernel ends. A status call that fails is counted, never read as a state.
started=$(date +%s); fails=0; result=2
while true; do
  state=$(kaggle kernels status "$OWNER/$KERNEL" 2>&1 | sed -n 's/.*KernelWorkerStatus\.\([A-Z_]*\).*/\1/p' | tail -1) || true
  if [ -z "$state" ]; then
    fails=$((fails + 1))
    [ "$fails" -lt "${KAGGLE_MAX_STATUS_FAILS:-5}" ] || { echo "kernel status failed $fails times" >&2; break; }
  else
    fails=0
    echo "$KERNEL: $state after $(($(date +%s) - started)) s"
    case "$state" in COMPLETE) result=0; break ;; ERROR | CANCEL*) result=1; break ;; esac
  fi
  [ $(($(date +%s) - started)) -lt "${KAGGLE_MAX_WAIT_SECONDS:-1500}" ] || { echo "gave up waiting for $KERNEL" >&2; break; }
  sleep "${KAGGLE_POLL_SECONDS:-30}"
done

kaggle kernels output "$OWNER/$KERNEL" -p "$OUTPUT_DIR" -o -q || echo "could not download the output of $KERNEL" >&2
echo "$OWNER/$KERNEL" > "$OUTPUT_DIR/kernel.txt"
if [ "$result" -ne 0 ]; then
  echo "kernel $OWNER/$KERNEL did not complete; the end of its log:" >&2
  tail -n 40 "$OUTPUT_DIR/$KERNEL.log" >&2 2>/dev/null || echo "(no log downloaded)" >&2
  exit "$result"
fi
echo "kernel $OWNER/$KERNEL complete, output in $OUTPUT_DIR"

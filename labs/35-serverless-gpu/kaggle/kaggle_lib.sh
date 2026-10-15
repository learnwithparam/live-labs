# shellcheck shell=bash
# Kaggle helpers, adapted from edge-avatar-engine's builder/kaggle_lib.sh. The token lives in ~/.kaggle,
# never on a command line. Bounds are overridable: KAGGLE_POLL_SECONDS, KAGGLE_MAX_WAIT_SECONDS,
# KAGGLE_MAX_STATUS_FAILS, KAGGLE_READY_SECONDS.

KAGGLE_CREATED=()

kaggle_owner() {
  local owner
  owner=$(kaggle config view | sed -n 's/^- username: //p')
  [ -n "$owner" ] || { echo "kaggle is not logged in: put the token in ~/.kaggle first" >&2; return 1; }
  echo "$owner"
}

# A name no earlier run used, so runs never collide on a dataset or notebook slug.
kaggle_unique() { echo "$1-$(date +%m%d%H%M%S)-$$"; }

# The EXIT trap: delete the datasets this run created, and the scratch folder.
kaggle_finish() {
  local dataset
  for dataset in ${KAGGLE_CREATED[@]+"${KAGGLE_CREATED[@]}"}; do kaggle datasets delete "$dataset" -y >/dev/null 2>&1 || true; done
  [ -z "${1:-}" ] || rm -rf "$1"
}

# kaggle_dataset <owner> <name> <folder>: a new private dataset, waited on until Kaggle has built it.
kaggle_dataset() {
  local owner=$1 name=$2 folder=$3 began fails=0 state
  printf '{"title": "%s", "id": "%s/%s", "licenses": [{"name": "other"}]}\n' "$name" "$owner" "$name" > "$folder/dataset-metadata.json"
  kaggle datasets create -p "$folder" --dir-mode zip || return 1
  KAGGLE_CREATED+=("$owner/$name")
  # A notebook pushed before its dataset is ready silently runs without it.
  began=$(date +%s)
  while true; do
    if state=$(kaggle datasets status "$owner/$name" 2>&1); then fails=0; else fails=$((fails + 1)); fi
    [ "$(echo "$state" | tail -1)" = "ready" ] && return 0
    [ "$fails" -lt "${KAGGLE_MAX_STATUS_FAILS:-5}" ] || { echo "dataset status failed $fails times: $state" >&2; return 1; }
    [ $(($(date +%s) - began)) -lt "${KAGGLE_READY_SECONDS:-900}" ] || { echo "dataset not ready in time: $state" >&2; return 1; }
    sleep "${KAGGLE_POLL_SECONDS:-30}"
  done
}

# kaggle_wait <owner/name>: 0 on COMPLETE, 1 on ERROR or CANCEL, 2 when the wait or the failed status calls run out.
kaggle_wait() {
  local ref=$1 began fails=0 output state
  began=$(date +%s)
  while true; do
    if output=$(kaggle kernels status "$ref" 2>&1); then
      state=$(echo "$output" | sed -n 's/.*KernelWorkerStatus\.\([A-Z_]*\).*/\1/p' | tail -1)
    else
      state=""
    fi
    if [ -z "$state" ]; then
      fails=$((fails + 1))
      echo "status call failed ($fails/${KAGGLE_MAX_STATUS_FAILS:-5}): $output" >&2
      [ "$fails" -lt "${KAGGLE_MAX_STATUS_FAILS:-5}" ] || return 2
    else
      fails=0
      echo "$ref: $state"
      case "$state" in COMPLETE) return 0 ;; ERROR | CANCEL*) return 1 ;; esac
    fi
    [ $(($(date +%s) - began)) -lt "${KAGGLE_MAX_WAIT_SECONDS:-1800}" ] || { echo "gave up waiting for $ref" >&2; return 2; }
    sleep "${KAGGLE_POLL_SECONDS:-30}"
  done
}

# kaggle_kernel <owner> <name> <dataset> <folder holding run.py> <out folder>: a T4 notebook with internet,
# stopped by Kaggle at 10 minutes whatever happens. Downloads the output even when the run failed.
kaggle_kernel() {
  local owner=$1 name=$2 dataset=$3 folder=$4 out=$5 code=0
  printf '{"id": "%s/%s", "title": "%s", "code_file": "run.py", "language": "python", "kernel_type": "script", "is_private": "true", "enable_gpu": "true", "enable_internet": "true", "dataset_sources": ["%s/%s"]}\n' \
    "$owner" "$name" "$name" "$owner" "$dataset" > "$folder/kernel-metadata.json"
  kaggle kernels push -p "$folder" --accelerator NvidiaTeslaT4 -t 600 || return 1
  kaggle_wait "$owner/$name" || code=$?
  mkdir -p "$out"
  kaggle kernels output "$owner/$name" -p "$out" || echo "could not download the output of $owner/$name" >&2
  if [ "$code" -ne 0 ]; then
    echo "notebook $owner/$name did not complete (code $code)" >&2
    [ -f "$out/server.log" ] && tail -n 40 "$out/server.log" >&2
    return "$code"
  fi
  ls -la "$out"
}

#!/usr/bin/env bash
set -euo pipefail

# Run tests in staggered shards to reduce memory/terminal pressure in VS Code.
# Usage:
#   bash tests/run_sharded_pytest.sh
#   bash tests/run_sharded_pytest.sh 10
#   bash tests/run_sharded_pytest.sh 10 "tests/test_*.py"
#   bash tests/run_sharded_pytest.sh 6 "tests/test_*.py" 2 true

BATCH_SIZE="${1:-8}"
PATTERN="${2:-tests/test_*.py}"
STAGGER_SECONDS="${3:-1}"
CONTINUE_ON_FAIL="${4:-false}"

if ! [[ "$BATCH_SIZE" =~ ^[0-9]+$ ]] || [[ "$BATCH_SIZE" -lt 1 ]]; then
  echo "BATCH_SIZE must be a positive integer" >&2
  exit 2
fi

if ! [[ "$STAGGER_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "STAGGER_SECONDS must be a non-negative integer" >&2
  exit 2
fi

if [[ "$CONTINUE_ON_FAIL" != "true" && "$CONTINUE_ON_FAIL" != "false" ]]; then
  echo "CONTINUE_ON_FAIL must be either true or false" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p .test-logs
LOG_FILE=".test-logs/sharded-pytest.log"
SUMMARY_FILE=".test-logs/sharded-pytest-summary.txt"
BATCH_LOG_DIR=".test-logs/sharded-batches"
mkdir -p "$BATCH_LOG_DIR"

: > "$LOG_FILE"
: > "$SUMMARY_FILE"

if [[ -f "venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
fi

FILES=()
while IFS= read -r file; do
  FILES+=("$file")
done < <(find tests -type f -name "$(basename "$PATTERN")" | sort)

if [[ "${#FILES[@]}" -eq 0 ]]; then
  echo "No tests matched pattern: $PATTERN" | tee -a "$SUMMARY_FILE"
  exit 1
fi

echo "Running ${#FILES[@]} files in batches of $BATCH_SIZE (stagger=${STAGGER_SECONDS}s, continue_on_fail=${CONTINUE_ON_FAIL})" | tee -a "$SUMMARY_FILE"

total_pass=0
total_fail=0
batch_index=0
failed_batches=()

for ((i=0; i<${#FILES[@]}; i+=BATCH_SIZE)); do
  batch_index=$((batch_index + 1))
  batch=("${FILES[@]:i:BATCH_SIZE}")
  batch_log_file="$BATCH_LOG_DIR/batch-$(printf "%03d" "$batch_index").log"

  echo "[batch $batch_index] ${#batch[@]} files -> $batch_log_file" | tee -a "$SUMMARY_FILE"
  {
    echo "===== BATCH $batch_index START ====="
    printf '%s\n' "${batch[@]}"
    python -m pytest -q --disable-warnings --maxfail=1 "${batch[@]}"
    echo "===== BATCH $batch_index END ====="
  } > "$batch_log_file" 2>&1 && {
    cat "$batch_log_file" >> "$LOG_FILE"
    total_pass=$((total_pass + 1))
    echo "[batch $batch_index] PASS" | tee -a "$SUMMARY_FILE"
    if [[ "$STAGGER_SECONDS" -gt 0 ]]; then
      echo "[batch $batch_index] cooldown ${STAGGER_SECONDS}s" | tee -a "$SUMMARY_FILE"
      sleep "$STAGGER_SECONDS"
    fi
    continue
  }

  cat "$batch_log_file" >> "$LOG_FILE"
  total_fail=$((total_fail + 1))
  failed_batches+=("$batch_index")
  echo "[batch $batch_index] FAIL (see $LOG_FILE)" | tee -a "$SUMMARY_FILE"
  tail -n 80 "$batch_log_file"

  if [[ "$CONTINUE_ON_FAIL" != "true" ]]; then
    break
  fi

  if [[ "$STAGGER_SECONDS" -gt 0 ]]; then
    echo "[batch $batch_index] cooldown ${STAGGER_SECONDS}s" | tee -a "$SUMMARY_FILE"
    sleep "$STAGGER_SECONDS"
  fi
done

echo "Batches passed: $total_pass" | tee -a "$SUMMARY_FILE"
echo "Batches failed: $total_fail" | tee -a "$SUMMARY_FILE"
if [[ "${#failed_batches[@]}" -gt 0 ]]; then
  echo "Failed batch indexes: ${failed_batches[*]}" | tee -a "$SUMMARY_FILE"
fi

if [[ "$total_fail" -gt 0 ]]; then
  exit 1
fi

echo "Sharded test run complete" | tee -a "$SUMMARY_FILE"

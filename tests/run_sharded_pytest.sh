#!/usr/bin/env bash
set -euo pipefail

# Run tests in small shards to reduce memory/terminal pressure in VS Code.
# Usage:
#   bash tests/run_sharded_pytest.sh
#   bash tests/run_sharded_pytest.sh 10
#   bash tests/run_sharded_pytest.sh 10 "tests/test_*.py"

BATCH_SIZE="${1:-8}"
PATTERN="${2:-tests/test_*.py}"

if ! [[ "$BATCH_SIZE" =~ ^[0-9]+$ ]] || [[ "$BATCH_SIZE" -lt 1 ]]; then
  echo "BATCH_SIZE must be a positive integer" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p .test-logs
LOG_FILE=".test-logs/sharded-pytest.log"
SUMMARY_FILE=".test-logs/sharded-pytest-summary.txt"

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

echo "Running ${#FILES[@]} files in batches of $BATCH_SIZE" | tee -a "$SUMMARY_FILE"

total_pass=0
total_fail=0
batch_index=0

for ((i=0; i<${#FILES[@]}; i+=BATCH_SIZE)); do
  batch_index=$((batch_index + 1))
  batch=("${FILES[@]:i:BATCH_SIZE}")

  echo "[batch $batch_index] ${#batch[@]} files" | tee -a "$SUMMARY_FILE"
  {
    echo "===== BATCH $batch_index START ====="
    printf '%s\n' "${batch[@]}"
    python -m pytest -q --disable-warnings --maxfail=1 "${batch[@]}"
    echo "===== BATCH $batch_index END ====="
  } >> "$LOG_FILE" 2>&1 && {
    total_pass=$((total_pass + 1))
    echo "[batch $batch_index] PASS" | tee -a "$SUMMARY_FILE"
    continue
  }

  total_fail=$((total_fail + 1))
  echo "[batch $batch_index] FAIL (see $LOG_FILE)" | tee -a "$SUMMARY_FILE"
  tail -n 80 "$LOG_FILE"
  break
done

echo "Batches passed: $total_pass" | tee -a "$SUMMARY_FILE"
echo "Batches failed: $total_fail" | tee -a "$SUMMARY_FILE"

if [[ "$total_fail" -gt 0 ]]; then
  exit 1
fi

echo "Sharded test run complete" | tee -a "$SUMMARY_FILE"

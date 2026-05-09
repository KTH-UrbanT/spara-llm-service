#!/usr/bin/env bash
# DO NOT refactor this into a single Python process.
# The 4-process layout is load-bearing — it ensures each arm's evaluator
# agent loads its arm-specific prompt at module import time.
# See plan-eil-v2.md §A.3 and plan-eil-v1.md §0.2.
#
# Sequencing rules (also load-bearing):
#   - A1 must complete BEFORE A2 starts (its aggregated_data cache is
#     reused via --cache-from). Bash `&&` chaining provides this; never
#     use `&` to background an arm.
#   - If RUN_OPTIONAL_ARMS=true, A3 then A4 run sequentially after A2.
#   - If any arm exits non-zero, stop and surface the error; do not
#     start a downstream arm.
#
# Usage:
#   bash scripts/run_experiment.sh --dataset src/config/questions.json
#   bash scripts/run_experiment.sh --dataset src/config/questions_smoke.json --run-id custom_id
#   RUN_OPTIONAL_ARMS=true bash scripts/run_experiment.sh --dataset src/config/questions.json

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing.
# ---------------------------------------------------------------------------
DATASET=""
RUN_ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dataset)
            DATASET="$2"; shift 2 ;;
        --run-id)
            RUN_ID="$2"; shift 2 ;;
        -h|--help)
            sed -n '1,30p' "$0"; exit 0 ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 --dataset <path> [--run-id <id>]" >&2
            exit 2 ;;
    esac
done

if [[ -z "$DATASET" ]]; then
    echo "ERROR: --dataset is required." >&2
    exit 2
fi

if [[ ! -f "$DATASET" ]]; then
    echo "ERROR: dataset file not found: $DATASET" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Default --run-id derivation.
# Smoke runs get a unique-per-second id so they don't clobber the full run on
# the same day. Full runs get a date-stamped v2 id.
# ---------------------------------------------------------------------------
if [[ -z "$RUN_ID" ]]; then
    if [[ "$DATASET" == *smoke* ]]; then
        RUN_ID="smoke_$(date +%s)"
    else
        RUN_ID="$(date +%Y-%m-%d)_v2"
    fi
fi

OUTPUT_DIR="artifacts/runs/${RUN_ID}"
PROTOCOL="experiments/protocol.yaml"

mkdir -p "$OUTPUT_DIR"

START_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================================="
echo "SPARA EIL experiment — run_id=${RUN_ID}"
echo "Dataset:    $DATASET"
echo "Output dir: $OUTPUT_DIR"
echo "Started:    $START_UTC"
if [[ "${RUN_OPTIONAL_ARMS:-false}" == "true" ]]; then
    echo "Mode:       primary + optional (A1 -> A2 -> A3 -> A4)"
else
    echo "Mode:       primary only (A1 -> A2)"
fi
echo "=========================================================="

# ---------------------------------------------------------------------------
# Per-arm runner. Each invocation is its own Python process — see header.
# ---------------------------------------------------------------------------
run_arm () {
    local arm="$1"
    local extra_flags="${2:-}"
    echo
    echo "------ Arm $arm ------"
    # shellcheck disable=SC2086 # extra_flags is intentionally word-split.
    python -m scripts.run_arm \
        --arm "$arm" \
        --dataset "$DATASET" \
        --run-id "${RUN_ID}_${arm}" \
        --output-dir "$OUTPUT_DIR" \
        $extra_flags
}

# A1 first; A1 has no --cache-from (it populates the cache as a side effect).
run_arm A1

# A2 reuses A1's aggregated_data cache. `&&` chaining → A2 only starts if A1
# exits zero (set -e also enforces this).
run_arm A2 "--cache-from ${OUTPUT_DIR}/A1"

ARMS_EXECUTED="A1,A2"

# Optional secondary arms.
if [[ "${RUN_OPTIONAL_ARMS:-false}" == "true" ]]; then
    run_arm A3 "--cache-from ${OUTPUT_DIR}/A1"
    run_arm A4 "--cache-from ${OUTPUT_DIR}/A1"
    ARMS_EXECUTED="A1,A2,A3,A4"
fi

END_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# Final bookkeeping. The Python helper does the SHA-256 + git work.
# ---------------------------------------------------------------------------
python -m scripts.write_run_record \
    --run-id "$RUN_ID" \
    --output-dir "$OUTPUT_DIR" \
    --arms "$ARMS_EXECUTED" \
    --protocol "$PROTOCOL" \
    --start-utc "$START_UTC" \
    --end-utc "$END_UTC"

echo
echo "=========================================================="
echo "Done. Arms executed: ${ARMS_EXECUTED}"
echo "Run record:          ${OUTPUT_DIR}/run_record.yaml"
echo "Finished:            $END_UTC"
echo "=========================================================="

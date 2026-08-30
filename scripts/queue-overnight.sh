#!/usr/bin/env bash
# Reference queue script for a lease-era CrucibleForge campaign (3.2.0+).
#
# This is the shape every unattended run on a shared rig should have, and it is
# the script that produced the top of the 2026-08-24 board (dark-scarlett-31b
# and dark-scarlett-27b-v2, full 251-case runs, both judged by the same 122B):
#
#   1. Source the environment IN-SHELL before anything else. StudioForge's
#      management API (`POST /api/leases`, load-recommended, settings) needs
#      X-MCP-Pin, and `models.yaml` references it as ${STUDIOFORGE_MCP_PIN};
#      a `${ENV}` that is not exported is a silent 403 mid-run.
#   2. Serialise on results/.rig.lock with flock, blocking. Two CrucibleForge runs
#      on one rig fight over the same GPUs even with leases, because the
#      lease holder is `crucibleforge` for both.
#   3. run -> judge PER MODEL, report ONCE at the end. Judging per model keeps
#      a late failure from costing you the earlier models' verdicts.
#   4. Check rc after every phase and exit non-zero with a distinct code.
#      3.2.0 made run/recover/judge exit non-zero on real failure precisely so
#      a wrapper like this can stamp DONE only on a clean exit -- never grep
#      the log for success.
#
# CRUCIBLEFORGE_ENV_FILE is REQUIRED — it names the file this sources for the
# lease PIN and provider API keys. There is deliberately no default: guessing a
# path off $HOME reads a file the script has no business knowing about.
#
# Override the defaults from the environment:
#   CRUCIBLEFORGE_ENV_FILE=/path/to/env MODELS="a b c" JUDGE="provider:model_id" \
#     scripts/queue-overnight.sh
set -u
cd "$(dirname "$0")/.." || exit 1

# 1. lease PIN + API keys
ENV_FILE="${CRUCIBLEFORGE_ENV_FILE:-}"
if [ -z "$ENV_FILE" ]; then
  echo "set CRUCIBLEFORGE_ENV_FILE=/path/to/env — the file holding STUDIOFORGE_MCP_PIN" \
       "and any provider API keys this campaign needs" >&2
  exit 2
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "CRUCIBLEFORGE_ENV_FILE=$ENV_FILE does not exist" >&2
  exit 2
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

JUDGE="${JUDGE:-studioforge:mradermacher/Qwen3.5-122B-A10B-heretic-v2-i1-GGUF/Qwen3.5-122B-A10B-heretic-v2.i1-Q5_K_M}"
MODELS="${MODELS:-dark-scarlett-31b dark-scarlett-27b-v2}"
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="results/bench_queue-${STAMP}.log"
mkdir -p results

# 2. serialise on the rig lock (blocking -- waits out any other crucibleforge run)
exec 9>results/.rig.lock
flock 9

exec > >(tee -a "$LOG") 2>&1

echo "=== CRUCIBLEFORGE QUEUE START $(date) models=[$MODELS] judge=$JUDGE ==="

# 3. run -> judge per model
for MODEL in $MODELS; do
  echo ""
  echo "=== MODEL $MODEL START $(date) ==="

  echo "=== PHASE run START $(date) ==="
  uv run crucibleforge run --models "$MODEL" --fresh --yes --judge "$JUDGE"
  rc=$?
  echo "=== PHASE run rc=$rc $(date) ==="
  [ "$rc" -ne 0 ] && { echo "=== FAILED $MODEL phase=run rc=$rc ==="; exit 11; }

  echo "=== PHASE judge START $(date) ==="
  uv run crucibleforge judge --models "$MODEL" --judge "$JUDGE"
  rc=$?
  echo "=== PHASE judge rc=$rc $(date) ==="
  [ "$rc" -ne 0 ] && { echo "=== FAILED $MODEL phase=judge rc=$rc ==="; exit 12; }

  echo "=== DONE $MODEL (run+judge) $(date) ==="
done

# 4. one report over the whole campaign
echo ""
echo "=== PHASE report START $(date) ==="
uv run crucibleforge report
rc=$?
echo "=== PHASE report rc=$rc $(date) ==="
[ "$rc" -ne 0 ] && { echo "=== FAILED phase=report rc=$rc ==="; exit 13; }

echo ""
echo "=== DONE all models: $MODELS $(date) ==="
exit 0

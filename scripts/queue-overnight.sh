#!/usr/bin/env bash
# Reference queue script for an unattended CrucibleForge campaign (3.3.0+).
#
# There is ONE benchmark (profiles/bench.yaml) and ONE command per model:
#
#   uv run crucibleforge all --models <label> --fresh --yes
#
# which loads the model once, generates every case, has the profile's judge (Gemma-4-31B heretic Q8) score
# the chat rows, and rebuilds the board (results/report.md, failures.md,
# report.html). This script only adds what a campaign needs around it:
#
#   1. Source the environment IN-SHELL before anything else. StudioForge's
#      management API (`POST /api/leases`, load-recommended, settings) needs
#      X-MCP-Pin, and `models.yaml` references it as ${STUDIOFORGE_MCP_PIN};
#      a `${ENV}` that is not exported is a silent 403 mid-run.
#   2. One benchmark at a time: the CLI takes results/.rig.lock itself and
#      waits for a running benchmark to finish (no flock needed here).
#   3. Check rc after every model and stop with a distinct code, so DONE is
#      stamped only on a clean exit — never grep the log for success.
#
# CRUCIBLEFORGE_ENV_FILE is REQUIRED — it names the file this sources for the
# lease PIN and provider API keys. There is deliberately no default: guessing a
# path off $HOME reads a file the script has no business knowing about.
#
#   CRUCIBLEFORGE_ENV_FILE=/path/to/env MODELS="a b c" scripts/queue-overnight.sh
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

MODELS="${MODELS:?set MODELS to the registry labels to bench}"
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="results/bench_queue-${STAMP}.log"
mkdir -p results

exec > >(tee -a "$LOG") 2>&1

echo "=== CRUCIBLEFORGE QUEUE START $(date) models=[$MODELS] ==="

for MODEL in $MODELS; do
  echo ""
  echo "=== MODEL $MODEL START $(date) ==="
  uv run crucibleforge all --models "$MODEL" --fresh --yes
  rc=$?
  echo "=== MODEL $MODEL rc=$rc $(date) ==="
  # one model failing (not served, engine can't load it, …) must not strand
  # the rest of an unattended campaign: record it and carry on
  [ "$rc" -ne 0 ] && { echo "=== FAILED $MODEL rc=$rc ==="; FAILED="${FAILED:-} $MODEL"; }
done

echo ""
if [ -n "${FAILED:-}" ]; then
  echo "=== DONE with failures:${FAILED} $(date) ==="
  exit 11
fi
echo "=== DONE all models: $MODELS $(date) ==="
exit 0

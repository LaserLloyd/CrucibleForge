#!/bin/bash
# P1: free Claude Code probe loop. Usage: probe/p1.sh <worktree> <family-spec>... [-- analyze args, e.g. --require keyfunc --core keyfunc=8]
#   probe/p1.sh /var/tmp/mb45-build/wt-keyfunc keyfunc:7,8 tracevm:7 -- --require keyfunc tracevm
# Haiku 5.5, Sonnet 5.5, Fable 5.2 on the Claude Code subscription; no judge, no time window (Jake lifted the limits);
# CF_CC_MAX_CALLS is only a runaway fuse. Each call gets its own results dir (and the table goes beside it).
set -u
PROBE="$(cd "$(dirname "$0")" && pwd)"
[ $# -ge 2 ] || { echo "usage: $0 <worktree> <family-spec>... [-- analyze args]" >&2; exit 2; }
WT="$(cd "$1" && pwd)" || exit 2; shift
FAM=(); while [ $# -gt 0 ] && [ "$1" != "--" ]; do FAM+=("$1"); shift; done
[ "${1:-}" = "--" ] && shift
export TMPDIR=/var/tmp/mb45-build/tmp-gen2p/run; export CRUCIBLEFORGE_CC_TMP=/var/tmp/mb45-build/tmp-gen2p/cc; mkdir -p "$TMPDIR"
export CF_CC_MAX_CALLS=3000 CF_CC_MAX_TOKENS=80000000 CF_CC_WINDOW=
export CRUCIBLEFORGE_CONFIG="$PROBE/models.yaml" CRUCIBLEFORGE_CC_EFFORT="${CRUCIBLEFORGE_CC_EFFORT:-high}"
unset ANTHROPIC_API_KEY
RES="/var/tmp/mb45-build/probe-results-gen2p-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$RES"
export CRUCIBLEFORGE_RESULTS="$RES"
cd "$WT" || exit 2
uv run python -I "$PROBE/make_probe.py" --worktree "$WT" --families "${FAM[@]}" --seeds "${P1_SEEDS:-p1,p2,p3}" || exit 2
# GEN2-P copy: the three models run side by side (one transcripts file each), 3 seeds by default
for m in ${P1_MODELS:-probe-haiku probe-sonnet probe-fable}; do
  ( uv run crucibleforge all --profile probe45 --models "$m" --no-judge --no-ladder --no-tiers --no-escalate \
    --yes --skip-conformance --allow-unpriced > "$RES/run-$m.log" 2>&1 || echo "WARN: $m rc=$?" ) &
done
wait
uv run python -I "$PROBE/analyze.py" --worktree "$WT" --results "$RES" --out "$RES/P1.md" "$@"
rc=$?; echo "table: $RES/P1.md  rc=$rc"; exit $rc

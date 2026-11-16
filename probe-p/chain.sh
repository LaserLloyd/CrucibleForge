#!/bin/bash
# one model: round A (patgen t1), then its own patgen t2 (turn-1 code in history)
set -u
m="$1"; WT=/var/tmp/mb45-build/wt-jx-p; P="$WT/probe-p"
NO_MAKE=1 PROFILE=probe45 P1_MODELS=probe-$m RES_TAG=A-$m bash "$P/p1.sh" "$WT" patgen-t1:2
A=$(ls -td /var/tmp/mb45-build/probe-results-jxp-A-$m-* | head -1)
cd "$WT" && uv run python -I "$P/make_t2.py" "$WT" "$m" "$A" || exit 3
NO_MAKE=1 PROFILE=probe45-$m P1_MODELS=probe-$m RES_TAG=B-$m bash "$P/p1.sh" "$WT" patgen-t2:2
echo CHAIN-DONE

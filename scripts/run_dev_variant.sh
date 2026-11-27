#!/usr/bin/env bash
# Run adapters on pgb-dev-v1 (train/holdout x clean/hard/control) with a given v2 guard dir.
# usage: scripts/run_dev_variant.sh TAG GUARD_DIR ADAPTERS [SPLITS]
set -euo pipefail
TAG=$1; GD=$2; AD=$3; SPLITS=${4:-"train holdout"}
P=${PGB_PYTHON:-/workspace/venvs/pgb/bin/python}
OUT=results/piiguard-v2/dev
mkdir -p "$OUT"
for S in $SPLITS; do for L in clean hard control; do
  PIIGUARD_V2_GUARD_DIR=$GD $P -m pii_guardbench.cli --prompts data/dev/pgb-dev-v1/$S.jsonl --layer $L \
    --adapters "$AD" --out "$OUT/$TAG-$S-$L.json" --out-dir "$OUT/$TAG-$S-$L" > "$OUT/$TAG-$S-$L.leaderboard.txt" 2>/dev/null
done; done

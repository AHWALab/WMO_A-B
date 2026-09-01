#!/usr/bin/env bash
# Freeze a copy of live training precip into offline_precips/
# (run once after a successful online hindcast that produced precip).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OFF="$ROOT/offline_precips"
mkdir -p "$OFF"
echo "Materializing offline precip archive → $OFF"
if [[ -d "$ROOT/EF5_conf/precip/stream_sat" ]]; then
  rsync -a --delete "$ROOT/EF5_conf/precip/stream_sat/" "$OFF/stream_sat/"
fi
if [[ -d "$ROOT/EF5_conf/precip/stormlab" ]]; then
  rsync -a --delete "$ROOT/EF5_conf/precip/stormlab/"  "$OFF/stormlab/"
fi
if [[ -d "$ROOT/EF5_conf/precip/imerg" ]]; then
  rsync -a --delete "$ROOT/EF5_conf/precip/imerg/"     "$OFF/imerg/"
fi
if [[ -d "$ROOT/EF5_conf/qpf_store" ]]; then
  rsync -a "$ROOT/EF5_conf/qpf_store/"        "$OFF/qpf_store/"
fi
# EF5-ready STREAM-Sat / StormLab tifs from the online run
if [[ -d "$ROOT/EF5_conf/precipEF5/antigua_30m" ]]; then
  mkdir -p "$OFF/precipEF5"
  rsync -a --delete "$ROOT/EF5_conf/precipEF5/antigua_30m/" "$OFF/precipEF5/antigua_30m/"
fi
du -sh "$OFF"/* 2>/dev/null || true
echo "Done. Use: TITO_RUNTIME=apptainer ./tito-run.sh hindcast \"2025-10-10 01:00\" \"2025-10-10 01:00\" --regions Antigua --offline"

#!/usr/bin/env bash
# Ingest every drop in data/incoming/ and precompute all worlds × strategies (plus the cached
# forecasts the what-if engine re-solves on). Use it whenever new worlds arrive.
#   bash deploy/refresh-worlds.sh              # then restarts dce-api if it is installed
set -euo pipefail
cd "$(dirname "$0")/.."
for w in data/incoming/*/; do
  [ -f "$w/manifest.json" ] || continue
  echo "ingest $w"; uv run dce ingest "$w" >/dev/null || { echo "  !! validation failed for $w (see data/processed/*/validation_report.json)"; }
done
uv run dce precompute
uv run dce narrate || true   # AI briefs (template if no LLM key)
if [ "${1:-}" != "--no-restart" ] && systemctl list-unit-files dce-api.service >/dev/null 2>&1; then
  sudo systemctl restart dce-api || true
fi

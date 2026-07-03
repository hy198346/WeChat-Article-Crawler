#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHD_DOMAIN="${WECHAT_LAUNCHD_DOMAIN:-gui/$(id -u)}"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
SERVICES=(
  "com.wechat.articlecrawler.analysis-queue"
  "com.wechat.articlecrawler.analysis-static"
  "com.wechat.articlecrawler.reanalyze-api"
)

mkdir -p "$ROOT/logs" "$ROOT/output" "$AGENTS_DIR"

for service in "${SERVICES[@]}"; do
  src="$ROOT/config/launchd/${service}.plist"
  dst="$AGENTS_DIR/${service}.plist"
  cp -f "$src" "$dst"
  launchctl bootout "$LAUNCHD_DOMAIN" "$dst" >/dev/null 2>&1 || true
  launchctl bootstrap "$LAUNCHD_DOMAIN" "$dst"
done

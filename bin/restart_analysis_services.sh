#!/bin/zsh
set -euo pipefail

LAUNCHD_DOMAIN="${WECHAT_LAUNCHD_DOMAIN:-gui/$(id -u)}"
SERVICES=(
  "com.wechat.articlecrawler.analysis-queue"
  "com.wechat.articlecrawler.analysis-static"
  "com.wechat.articlecrawler.reanalyze-api"
)

for service in "${SERVICES[@]}"; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] restart ${LAUNCHD_DOMAIN}/${service}"
  launchctl kickstart -k "${LAUNCHD_DOMAIN}/${service}"
done

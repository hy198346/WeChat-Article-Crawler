#!/bin/zsh
set -euo pipefail

ROOT="/Users/chenwangqian/trae/WeChat-Article-Crawler"
if [[ -n "${WECHAT_ENV_FILE:-}" && -f "${WECHAT_ENV_FILE}" ]]; then
  set -a
  source "${WECHAT_ENV_FILE}"
  set +a
fi

WECOM_WEBHOOK_URL="${WECOM_WEBHOOK_URL:-${WECOM_WEBHOOKURL:-${WECOM_WEBHOOK:-}}}"
cd "$ROOT"

mkdir -p "$ROOT/logs"

export WECHAT_PROFILE_DIR="./my_wechat_profile"
export WECHAT_HEADLESS="1"
export WECHAT_REFRESH_MAX_WAIT="900"
export WECHAT_RUN_MODE="push-latest-all"
export WECHAT_SKIP_INSTALL="1"
export WECHAT_FORCE_PUSH="1"

# 告警函数
alert_fail() {
  local msg="$1"
  local level="${2:-⚠️}"
  if [[ -n "${WECOM_WEBHOOK_URL:-}" ]]; then
    curl -s -X POST "$WECOM_WEBHOOK_URL" \
      -H 'Content-Type: application/json' \
      -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"${level} WeChat-Article-Crawler\\n${msg}\\n时间: $(date '+%Y-%m-%d %H:%M')\\n修复: 在Trae中手动运行 python3 bootstrap_refresh_auth.py 扫码刷新token\"}}" \
      >/dev/null 2>&1
  fi

  ALERT_LEVEL="$level" ALERT_MSG="$msg" /usr/bin/python3 - <<'PY' >/dev/null 2>&1 || true
import os
from datetime import datetime

try:
    from wechat_crawler import send_serverchan_message
except Exception:
    raise SystemExit(0)

sendkey = (os.environ.get("SERVERCHAN_SENDKEY") or "").strip()
if not sendkey:
    raise SystemExit(0)

level = os.environ.get("ALERT_LEVEL", "⚠️")
msg = os.environ.get("ALERT_MSG", "")
now = datetime.now().strftime("%Y-%m-%d %H:%M")
title = f"{level} WeChat-Article-Crawler"
desp = f"{msg}\n\n时间: {now}\n\n修复: 在Trae中手动运行 python3 bootstrap_refresh_auth.py 扫码刷新token"
send_serverchan_message(sendkey, title, desp)
PY
}

# 执行主脚本，捕获输出用于检查
LOG_FILE=$(mktemp)
trap "rm -f '$LOG_FILE'" EXIT

EXIT_CODE=0
if command -v pwsh >/dev/null 2>&1; then
  /usr/bin/python3 "$ROOT/wechat_crawler.py" --push-latest-all --force --no-save-markdown > "$LOG_FILE" 2>&1 || EXIT_CODE=$?
else
  /usr/bin/python3 "$ROOT/wechat_crawler.py" --push-latest-all --force --no-save-markdown > "$LOG_FILE" 2>&1 || EXIT_CODE=$?
fi

cp "$LOG_FILE" "$ROOT/logs/run_project_launchd.last.log" 2>/dev/null || true
tail -n 200 "$LOG_FILE" || true

# 退出码非0: 脚本崩溃
if [ "$EXIT_CODE" -ne 0 ]; then
  alert_fail "退出码: ${EXIT_CODE}" "❌"
# 退出码0但存在 invalid session: token 过期
elif grep -q "invalid session" "$LOG_FILE"; then
  SESSION_COUNT=$(grep -c "invalid session" "$LOG_FILE")
  alert_fail "token 已过期，${SESSION_COUNT}次 invalid session"
fi

exit "$EXIT_CODE"

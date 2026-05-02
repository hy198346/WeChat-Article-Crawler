#!/bin/zsh
set -euo pipefail

ROOT="/Users/chenwangqian/trae/WeChat-Article-Crawler"
DEFAULT_ENV_FILE="$ROOT/.env"
if [[ -z "${WECHAT_ENV_FILE:-}" && -f "${DEFAULT_ENV_FILE}" ]]; then
  WECHAT_ENV_FILE="${DEFAULT_ENV_FILE}"
fi
if [[ -n "${WECHAT_ENV_FILE:-}" && -f "${WECHAT_ENV_FILE}" ]]; then
  set -a
  source "${WECHAT_ENV_FILE}"
  set +a
fi

WECOM_WEBHOOK_URL="${WECOM_WEBHOOK_URL:-${WECOM_WEBHOOKURL:-${WECOM_WEBHOOK:-}}}"
cd "$ROOT"

mkdir -p "$ROOT/logs"

export WECHAT_PROFILE_DIR="${WECHAT_PROFILE_DIR:-./my_wechat_profile}"
export WECHAT_HEADLESS="${WECHAT_HEADLESS:-1}"
export WECHAT_REFRESH_MAX_WAIT="${WECHAT_REFRESH_MAX_WAIT:-900}"
export WECHAT_RUN_MODE="${WECHAT_RUN_MODE:-push-latest-all}"
export WECHAT_SKIP_INSTALL="${WECHAT_SKIP_INSTALL:-1}"
export WECHAT_FORCE_PUSH="${WECHAT_FORCE_PUSH:-1}"
export WECHAT_FORCE_REFRESH="${WECHAT_FORCE_REFRESH:-0}"

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

alert_info_once() {
  local msg="$1"
  local key="$2"
  local ttl="${3:-21600}"
  ALERT_LEVEL="ℹ️" ALERT_MSG="$msg" ALERT_ONCE_KEY="$key" ALERT_ONCE_TTL="$ttl" /usr/bin/python3 - <<'PY' >/dev/null 2>&1 || true
import os
from datetime import datetime

try:
    from wechat_crawler import send_serverchan_message_once
except Exception:
    raise SystemExit(0)

sendkey = (os.environ.get("SERVERCHAN_SENDKEY") or "").strip()
if not sendkey:
    raise SystemExit(0)

level = os.environ.get("ALERT_LEVEL", "ℹ️")
msg = os.environ.get("ALERT_MSG", "")
dedupe_key = (os.environ.get("ALERT_ONCE_KEY") or "").strip()
ttl = int(os.environ.get("ALERT_ONCE_TTL") or "21600")
now = datetime.now().strftime("%Y-%m-%d %H:%M")
title = f"{level} WeChat-Article-Crawler"
desp = f"{msg}\n\n时间: {now}"
send_serverchan_message_once(sendkey, title, desp, dedupe_key=dedupe_key, ttl_seconds=ttl)
PY
}

AUTH_MARKER_RE='WECHAT_AUTH_EXPIRED|invalid session'
PHASE_AUTH_EXPIRED=0

run_phase() {
  local phase="$1"
  shift
  local phase_log
  phase_log=$(mktemp)
  local code=0
  echo "" >> "$LOG_FILE"
  echo "[launchd] ${phase} start: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  set +e
  "$@" >> "$phase_log" 2>&1
  code=$?
  set -e
  cat "$phase_log" >> "$LOG_FILE"
  echo "" >> "$LOG_FILE"
  echo "[launchd] ${phase} end: $(date '+%Y-%m-%d %H:%M:%S') exit=${code}" >> "$LOG_FILE"
  if grep -Eq "$AUTH_MARKER_RE" "$phase_log"; then
    PHASE_AUTH_EXPIRED=1
  else
    PHASE_AUTH_EXPIRED=0
  fi
  rm -f "$phase_log" 2>/dev/null || true
  return "$code"
}

LOG_FILE=$(mktemp)
trap "rm -f '$LOG_FILE'" EXIT

EXIT_CODE=0
AUTH_EXPIRED_FINAL=0

echo "[launchd] start: $(date '+%Y-%m-%d %H:%M:%S')" > "$LOG_FILE"
run_phase "main" /usr/bin/python3 "$ROOT/bootstrap_refresh_auth.py" || EXIT_CODE=$?
AUTH_EXPIRED_MAIN="$PHASE_AUTH_EXPIRED"
AUTH_EXPIRED_FINAL="$AUTH_EXPIRED_MAIN"

if [ "${AUTH_EXPIRED_MAIN}" -eq 1 ]; then
  SESSION_COUNT=$(grep -Ec "$AUTH_MARKER_RE" "$LOG_FILE" || echo "1")
  alert_info_once "检测到 token/cookie 可能已失效（${SESSION_COUNT}处认证异常），将自动刷新并重试" "auth_expired_detected" "${WECHAT_AUTH_EXPIRED_ALERT_TTL_SECONDS:-21600}"

  run_phase "refresh-only" env WECHAT_RUN_MODE="refresh-only" WECHAT_FORCE_REFRESH="1" /usr/bin/python3 "$ROOT/bootstrap_refresh_auth.py" || EXIT_CODE=$?
  if [ "$EXIT_CODE" -eq 0 ]; then
    run_phase "main-retry" env WECHAT_RUN_MODE="push-latest-all" WECHAT_FORCE_REFRESH="0" /usr/bin/python3 "$ROOT/bootstrap_refresh_auth.py" || EXIT_CODE=$?
    AUTH_EXPIRED_FINAL="$PHASE_AUTH_EXPIRED"
  fi
fi

cp "$LOG_FILE" "$ROOT/logs/run_project_launchd.last.log" 2>/dev/null || true
tail -n 200 "$LOG_FILE" || true

# 退出码非0: 脚本崩溃
if [ "$EXIT_CODE" -ne 0 ]; then
  alert_fail "退出码: ${EXIT_CODE}" "❌"
# 退出码0但最终仍有认证异常
elif [ "${AUTH_EXPIRED_FINAL}" -eq 1 ]; then
  SESSION_COUNT=$(grep -Ec "$AUTH_MARKER_RE" "$LOG_FILE" || echo "1")
  alert_fail "token/cookie 可能已过期（${SESSION_COUNT}处认证异常），自动刷新仍未恢复"
fi

exit "$EXIT_CODE"

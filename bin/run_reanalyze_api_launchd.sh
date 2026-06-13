#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_ENV_FILE="$ROOT/.env"
ENV_FILE="${WECHAT_ENV_FILE:-}"

load_env_file() {
  local env_path="$1"
  /usr/bin/python3 - "$env_path" <<'PY'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    for raw_line in fh:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        print(f"{key}\0{value}\0", end="")
PY
}

if [[ -n "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "WECHAT_ENV_FILE does not exist: $ENV_FILE" >&2
    exit 1
  fi
elif [[ -f "$DEFAULT_ENV_FILE" ]]; then
  export WECHAT_ENV_FILE="$DEFAULT_ENV_FILE"
fi

if [[ -n "${WECHAT_ENV_FILE:-}" ]]; then
  while IFS= read -r -d '' env_key && IFS= read -r -d '' env_value; do
    export "${env_key}=${env_value}"
  done < <(load_env_file "$WECHAT_ENV_FILE")
fi

mkdir -p "$ROOT/logs"
cd "$ROOT"

exec /usr/bin/python3 "$ROOT/scripts/wechat_article_crawler/wechat_crawler.py" --serve-reanalyze

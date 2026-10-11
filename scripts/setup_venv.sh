#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
#
# 创建/更新项目的 .venv(已满足时直接跳过)。由 setup.sh 调用,也可单独运行
# (make ha-test 只需要它,不必下载 ESP-IDF 和 Matter SDK)。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 项目自己的 Python 环境(脚本、HA 集成测试共用一个),不与 ESP-IDF / Pigweed 的 venv 混用。
# HA 2026.9 要求 Python >= 3.14.2,所以整个 .venv 都用 3.14
find_python() {
  local c
  for c in python3.14 /opt/homebrew/bin/python3.14 /usr/local/bin/python3.14 python3; do
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 14, 2))' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

VENV_OK=0
if [ -x "$ROOT/.venv/bin/python" ] \
   && "$ROOT/.venv/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 14, 2))' 2>/dev/null; then
  VENV_OK=1
fi
if [ "$VENV_OK" = 0 ]; then
  PYBIN="$(find_python)" || { echo "错误:需要 Python >= 3.14.2(brew install python@3.14)"; exit 1; }
  echo "创建 .venv(用 $PYBIN)"
  rm -rf "$ROOT/.venv"
  "$PYBIN" -m venv "$ROOT/.venv"
fi

# 依赖文件没变就跳过 pip(HA 测试依赖很大)
REQS=("$ROOT/scripts/requirements.txt" "$ROOT/homeassistant/requirements-ha-test.txt")
REQ_STAMP="$ROOT/.venv/.requirements.stamp"
req_hash="$(cat "${REQS[@]}" | shasum | cut -d' ' -f1)"
if [ "$req_hash" != "$(cat "$REQ_STAMP" 2>/dev/null)" ]; then
  echo "安装 Python 依赖 ..."
  "$ROOT/.venv/bin/pip" install -q -r "${REQS[0]}" -r "${REQS[1]}"
  echo "$req_hash" > "$REQ_STAMP"
fi

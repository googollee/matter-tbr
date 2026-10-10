#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
#
# Apple Silicon 专用修复,在 Matter SDK bootstrap 之后运行(可重复执行):
#   1. zap-cli:SDK 给所有 arm64 Mac 装的是 x86_64 版,换成官方原生 arm64 包
#   2. cryptography:Pigweed 自带 Python 的版本号带 +chromium 后缀,
#      与 cryptography 43 的 Rust 绑定冲突,降级到 41.0.7
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$ROOT/.deps/connectedhomeip/.environment"
ZAP_VERSION="v2026.09.09"
ZAPDIR="$ENV_DIR/cipd/packages/zap"
PIP="$ENV_DIR/pigweed-venv/bin/pip"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "不是 Apple Silicon Mac,无需修复。"
  exit 0
fi

if [ ! -x "$ZAPDIR/zap-cli" ] || [ ! -x "$PIP" ]; then
  echo "错误:未找到 Matter 环境,请先运行 scripts/setup.sh 并完成 bootstrap:" >&2
  echo "  cd .deps/connectedhomeip && source scripts/bootstrap.sh -p all,esp32" >&2
  exit 1
fi

# 1. zap-cli
if [[ "$(file "$ZAPDIR/zap-cli")" == *arm64* ]]; then
  echo "[zap-cli] 已是 arm64,跳过。"
else
  echo "[zap-cli] 替换为 arm64 版 $ZAP_VERSION ..."
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fSL -o "$TMP/zap.zip" \
    "https://github.com/project-chip/zap/releases/download/$ZAP_VERSION/zap-mac-arm64.zip"
  unzip -q "$TMP/zap.zip" -d "$TMP"
  chmod u+w "$ZAPDIR" "$ZAPDIR/zap-cli"
  cp "$TMP/zap-cli" "$ZAPDIR/zap-cli"
  chmod +x "$ZAPDIR/zap-cli"
  rm -rf "$ZAPDIR/zap.app"
  cp -R "$TMP/zap.app" "$ZAPDIR/zap.app"
  xattr -cr "$ZAPDIR/zap-cli" "$ZAPDIR/zap.app"
  "$ZAPDIR/zap-cli" --version | head -1
fi

# 2. cryptography
CRYPTO_VER="$("$PIP" show cryptography 2>/dev/null | sed -n 's/^Version: //p')"
if [ "$CRYPTO_VER" = "41.0.7" ]; then
  echo "[cryptography] 已是 41.0.7,跳过。"
else
  echo "[cryptography] 安装 41.0.7 ..."
  "$PIP" install -q "cryptography==41.0.7"
fi

echo "修复完成。"

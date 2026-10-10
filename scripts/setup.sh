#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
#
# 下载/链接官方依赖到 .deps/。已存在的目录(含符号链接)会被跳过。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS="$ROOT/.deps"

IDF_VERSION="v5.5.4"
MATTER_COMMIT="d305b076195"   # connectedhomeip,已验证可编译的版本

mkdir -p "$DEPS"

if [ ! -e "$DEPS/esp-idf" ]; then
  git clone -b "$IDF_VERSION" --recursive https://github.com/espressif/esp-idf.git "$DEPS/esp-idf"
  (cd "$DEPS/esp-idf" && ./install.sh esp32s3,esp32h2 && python3 tools/idf_tools.py install cmake ninja)
fi

if [ ! -e "$DEPS/connectedhomeip" ]; then
  git clone https://github.com/project-chip/connectedhomeip.git "$DEPS/connectedhomeip"
  (cd "$DEPS/connectedhomeip" \
    && git checkout "$MATTER_COMMIT" \
    && python3 scripts/checkout_submodules.py --shallow --platform esp32 linux)
  echo "下一步:cd $DEPS/connectedhomeip && source scripts/bootstrap.sh -p all,esp32"
  echo "Apple Silicon 在 bootstrap 之后还需运行:./scripts/fix_on_mac.sh"
fi

# 项目自己的 Python 环境(gen_commissioning.py 等脚本用),不与 ESP-IDF / Pigweed 的 venv 混用
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/scripts/requirements.txt"

# 固件通过 third_party/connectedhomeip 引用 Matter SDK
mkdir -p "$ROOT/firmware/third_party"
ln -sfn ../../.deps/connectedhomeip "$ROOT/firmware/third_party/connectedhomeip"
echo "依赖就绪:$DEPS"

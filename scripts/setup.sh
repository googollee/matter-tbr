#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
#
# 下载/链接官方依赖到 .deps/。已存在的目录(含符号链接)会被跳过。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS="$ROOT/.deps"

IDF_VERSION="v5.5.5"
MATTER_COMMIT="v1.7-te2"   # connectedhomeip,已验证可编译的版本(tag)

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

"$ROOT/scripts/setup_venv.sh"

# 固件通过 third_party/connectedhomeip 引用 Matter SDK
mkdir -p "$ROOT/firmware/third_party"
ln -sfn ../../.deps/connectedhomeip "$ROOT/firmware/third_party/connectedhomeip"
echo "依赖就绪:$DEPS"

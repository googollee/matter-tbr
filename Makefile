# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
#
#   make build                   准备依赖、生成配对数据并编译完整固件
#                                (先 H2 的 RCP,再 S3 主固件,RCP 会被打包进镜像)
#   make deps                    只准备依赖(setup.sh、bootstrap、fix_on_mac.sh)
#   make config                  只根据 config/ 生成 factory 分区镜像和固件 sdkconfig 片段
#   make flash PORT=/dev/cu.xxx  烧录主固件和 factory 分区
#   make flash-factory PORT=...  只烧录 factory 分区
#   make clean                   清除所有编译中间产物和生成结果(不动 .deps/ 和 .venv/)
#

SHELL := /bin/bash

ROOT      := $(CURDIR)
IDF_DIR   := $(ROOT)/.deps/esp-idf
MATTER    := $(ROOT)/.deps/connectedhomeip
RCP_DIR   := $(IDF_DIR)/examples/openthread/ot_rcp
FW_DIR    := $(ROOT)/firmware
PY        := $(ROOT)/.venv/bin/python
OUT       := $(ROOT)/out

# 必须先 source ESP-IDF,再 source Matter 环境,顺序不能反
IDF_ENV    := . $(IDF_DIR)/export.sh >/dev/null
MATTER_ENV := . $(MATTER)/scripts/activate.sh >/dev/null

# 这两个文件决定 sdkconfig 的内容;IDF 只在 sdkconfig 不存在时才应用 defaults,
# 所以它们变了就要删掉旧的 sdkconfig 重新生成
CONFIG_INPUTS := $(FW_DIR)/sdkconfig.defaults $(FW_DIR)/sdkconfig.device
STAMP         := $(FW_DIR)/.sdkconfig.stamp

.PHONY: build deps config rcp firmware flash flash-factory clean

build: firmware

# 准备依赖:下载官方依赖和建 .venv -> Matter bootstrap(仅缺失时) -> Apple Silicon 修复
# 三步都可重复执行,已就绪时会自动跳过
deps:
	$(ROOT)/scripts/setup.sh
	@test -d $(MATTER)/.environment/cipd || { \
	  echo "运行 Matter bootstrap ..."; \
	  cd $(MATTER) && source scripts/bootstrap.sh -p all,esp32; }
	$(ROOT)/scripts/fix_on_mac.sh

# 根据 config/ 生成 out/factory_partition.bin 和 firmware/sdkconfig.device
config: deps
	$(PY) $(ROOT)/scripts/gen_commissioning.py

# ESP32-H2 的 802.15.4 RCP 固件
rcp: deps
	$(IDF_ENV) && cd $(RCP_DIR) \
	  && { test -f sdkconfig || idf.py set-target esp32h2; } \
	  && idf.py build

# ESP32-S3 主固件,会自动检测并打包上一步的 RCP
firmware: rcp config
	@new=$$(cat $(CONFIG_INPUTS) | shasum | cut -d' ' -f1); \
	if [ "$$new" != "$$(cat $(STAMP) 2>/dev/null)" ]; then \
	  echo "sdkconfig.defaults 或 config/ 有变化,重新生成 sdkconfig"; \
	  rm -f $(FW_DIR)/sdkconfig; \
	fi; \
	$(IDF_ENV) && $(MATTER_ENV) && cd $(FW_DIR) \
	  && { test -f sdkconfig || idf.py set-target esp32s3; } \
	  && idf.py build \
	  && echo "$$new" > $(STAMP)

# 以下两个目标需要 PORT,macOS 下用 ls /dev/cu.usbmodem* 确认(接 S3 口)
flash: firmware flash-factory-check
	$(IDF_ENV) && $(MATTER_ENV) && cd $(FW_DIR) && idf.py -p $(PORT) flash
	$(MAKE) flash-factory PORT=$(PORT)

flash-factory: flash-factory-check config
	$(IDF_ENV) && python $(IDF_DIR)/components/partition_table/parttool.py -p $(PORT) \
	  write_partition --partition-name fctry --input $(OUT)/factory_partition.bin

.PHONY: flash-factory-check
flash-factory-check:
	@test -n "$(PORT)" || { echo "请指定串口,例如 make flash PORT=/dev/cu.usbmodemXXXX"; exit 1; }

clean:
	rm -rf $(FW_DIR)/build $(FW_DIR)/managed_components \
	       $(FW_DIR)/sdkconfig $(FW_DIR)/sdkconfig.old \
	       $(FW_DIR)/sdkconfig.device $(STAMP) $(OUT)
	rm -rf $(RCP_DIR)/build $(RCP_DIR)/managed_components \
	       $(RCP_DIR)/sdkconfig $(RCP_DIR)/sdkconfig.old

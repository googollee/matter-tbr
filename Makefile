# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
#
# 运行 make help 查看所有命令。
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

COMPONENT_SRC := $(ROOT)/homeassistant/custom_components/thread_br

.DEFAULT_GOAL := build

.PHONY: help build deps venv config rcp firmware flash flash-factory clean component ha-test

help: ## 列出所有命令
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  make %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "  变量: PORT=/dev/cu.usbmodemXXXX (flash 系列)  ARGS=\"pytest 参数\" (ha-test)"

build: firmware ## 准备依赖、生成配对数据并编译完整固件(RCP + 主固件)

# 准备依赖:下载官方依赖和建 .venv -> Matter bootstrap(仅缺失时) -> Apple Silicon 修复
# 三步都可重复执行,已就绪时会自动跳过
deps: ## 只准备依赖(SDK、bootstrap、Mac 修复、.venv)
	$(ROOT)/scripts/setup.sh
	@test -d $(MATTER)/.environment/cipd || { \
	  echo "运行 Matter bootstrap ..."; \
	  cd $(MATTER) && source scripts/bootstrap.sh -p all,esp32; }
	$(ROOT)/scripts/fix_on_mac.sh

# 只准备项目 .venv(Python >= 3.14.2,含 HA 测试依赖),已就绪时跳过
venv: ## 只准备 .venv(Python >= 3.14.2)
	$(ROOT)/scripts/setup_venv.sh

# 根据 config/ 生成 out/factory_partition.bin 和 firmware/sdkconfig.device
config: deps ## 根据 config/ 生成 factory 分区镜像和 sdkconfig 片段
	$(PY) $(ROOT)/scripts/gen_commissioning.py

# ESP32-H2 的 802.15.4 RCP 固件
rcp: deps ## 只编译 ESP32-H2 的 RCP 固件
	$(IDF_ENV) && cd $(RCP_DIR) \
	  && { test -f sdkconfig || idf.py set-target esp32h2; } \
	  && idf.py build

# ESP32-S3 主固件,会自动检测并打包上一步的 RCP
firmware: rcp config ## 编译 ESP32-S3 主固件(同 build)
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
flash: firmware flash-factory-check ## 烧录主固件和 factory 分区(需要 PORT)
	$(IDF_ENV) && $(MATTER_ENV) && cd $(FW_DIR) && idf.py -p $(PORT) flash
	$(MAKE) flash-factory PORT=$(PORT)

flash-factory: flash-factory-check config ## 只烧录 factory 分区(需要 PORT)
	$(IDF_ENV) && python $(IDF_DIR)/components/partition_table/parttool.py -p $(PORT) \
	  write_partition --partition-name fctry --input $(OUT)/factory_partition.bin

.PHONY: flash-factory-check
flash-factory-check:
	@test -n "$(PORT)" || { echo "请指定串口,例如 make flash PORT=/dev/cu.usbmodemXXXX"; exit 1; }

clean: ## 清除编译产物和 out/(不动 .deps/ 和 .venv/)
	rm -rf $(FW_DIR)/build $(FW_DIR)/managed_components \
	       $(FW_DIR)/sdkconfig $(FW_DIR)/sdkconfig.old \
	       $(FW_DIR)/sdkconfig.device $(STAMP) $(OUT)
	rm -rf $(RCP_DIR)/build $(RCP_DIR)/managed_components \
	       $(RCP_DIR)/sdkconfig $(RCP_DIR)/sdkconfig.old

# HA 集成是纯 Python,没有编译步骤:检查语法和 JSON,再打包成可直接复制到
# HA 配置目录 custom_components/ 下的目录和 zip(不含 __pycache__)
component: venv ## 检查并打包 HA 集成到 out/thread_br/ 和 .zip
	$(PY) -c "import ast, json, pathlib; src = pathlib.Path('$(COMPONENT_SRC)'); \
	  [ast.parse(f.read_text(), str(f)) for f in src.rglob('*.py')]; \
	  [json.loads(f.read_text()) for f in src.rglob('*.json')]"
	rm -rf $(OUT)/thread_br $(OUT)/thread_br.zip
	mkdir -p $(OUT)
	rsync -a --exclude '__pycache__' --exclude '*.pyc' $(COMPONENT_SRC)/ $(OUT)/thread_br/
	cd $(OUT) && zip -qr thread_br.zip thread_br
	@echo "已生成 $(OUT)/thread_br/ 和 $(OUT)/thread_br.zip;复制到 HA 的 custom_components/ 后重启 HA"

ha-test: venv ## 跑 HA 集成的全部测试(ARGS 传给 pytest)
	$(PY) -m pytest homeassistant/tests -o asyncio_mode=auto $(ARGS)

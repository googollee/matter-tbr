# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""扫描集成源码里已知的、HA 已弃用的接口用法(与测试环境里的 HA 版本无关,纯文件检查)。

动机:HA 对自定义集成用到的弃用接口只在运行时记 WARNING,而且警告是逐版本加的。
实际遇到过:DeviceEntry.config_entries 在 2026.10.1 才开始报警,而测试环境是 2026.9.4,测不出来。
发现新的弃用用法后,把它加进下面的表里,就不会再回来。
"""

from pathlib import Path
import re

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "thread_br"

# (正则, 说明)。只扫源码里的非注释行
DEPRECATED = [
    (r"(?<!hass)(?<!homeassistant)\.config_entries\b(?!\.)", "DeviceEntry.config_entries 已弃用,设备只属于一个配置条目,用 config_entry_id(HA 2027.10 起失效)"),
    (r"\.async_get_device\(", "device_registry.async_get_device 已弃用,标识不再跨配置条目唯一,用 async_get_device_by_identifier(标识, 配置条目 id)"),
    (r"\bvia_device\s*=", "via_device 已弃用,只传 via_device_id"),
]


def source_lines():
    for source in sorted(PACKAGE.glob("*.py")):
        for number, line in enumerate(source.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if code.strip():
                yield source.name, number, code


@pytest.mark.parametrize(("pattern", "why"), DEPRECATED, ids=[w.split(" ")[0] for _, w in DEPRECATED])
def test_no_deprecated_api_usage(pattern, why):
    hits = [f"{name}:{number}: {code.strip()}" for name, number, code in source_lines() if re.search(pattern, code)]
    assert hits == [], f"{why}\n" + "\n".join(hits)


def test_the_check_itself_would_catch_the_known_case():
    """自检:正则必须能匹配到 2026.10.1 上被报警的那行代码,且不误伤 hass.config_entries。"""
    pattern = DEPRECATED[0][0]
    assert re.search(pattern, "if self.entry.entry_id in device.config_entries:")
    assert not re.search(pattern, "self.hass.config_entries.async_loaded_entries(DOMAIN)")
    assert not re.search(pattern, "hass.config_entries.async_update_entry(entry)")
    assert not re.search(pattern, "from homeassistant.config_entries import ConfigEntry")

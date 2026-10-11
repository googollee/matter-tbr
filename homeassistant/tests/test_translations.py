# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""翻译和服务描述的完整性检查(纯文件检查,不需要 HA)。"""

import json
from pathlib import Path
import re

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "thread_br"


def load(name):
    return json.loads((PACKAGE / "translations" / f"{name}.json").read_text())


def key_paths(data, prefix=()):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from key_paths(value, prefix + (key,))
        else:
            yield prefix + (key,)


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


def test_languages_have_the_same_keys():
    assert set(key_paths(load("en"))) == set(key_paths(load("zh-Hans")))


def test_placeholders_match_between_languages():
    en, zh = load("en"), load("zh-Hans")
    for path in key_paths(en):
        a, b = en, zh
        for part in path:
            a, b = a[part], b[part]
        assert placeholders(a) == placeholders(b), path


def test_every_translation_key_used_in_code_exists():
    en = load("en")
    used = set()
    for source in PACKAGE.glob("*.py"):
        used |= set(re.findall(r'translation_key="(\w+)"', source.read_text()))
    exceptions = set(en["exceptions"])
    missing = {k for k in used if k not in exceptions and k not in en["device"]}
    assert not missing, f"代码里用到但翻译里没有:{missing}"


def test_error_messages_use_only_provided_placeholders():
    """异常翻译里的占位符必须与代码传入的一致,否则 HA 渲染时会报错。"""
    en = load("en")["exceptions"]
    provided = {"invalid_parameter": {"reason"}, "create_failed": {"reason"}, "credentials_not_saved": {"reason"},
                "sync_failed": {"reason"}, "change_failed": {"reason"}, "change_not_switched": {"reason"},
                "already_on_channel": {"channel"}, "adopt_failed": {"reason"}, "adopt_not_switched": {"reason"}}
    for key, entry in en.items():
        assert placeholders(entry["message"]) == provided.get(key, set()), key


@pytest.mark.parametrize("language", ["en", "zh-Hans"])
def test_services_and_fields_are_described(language):
    services = load(language)["services"]
    assert set(services) == {"create_network", "adopt_network", "change_channel", "sync_network"}
    assert set(services["create_network"]["fields"]) == {"network_name", "channel", "passphrase"}
    assert set(services["change_channel"]["fields"]) == {"channel", "delay"}
    assert set(services["adopt_network"]["fields"]) == {"source_device", "delay"}
    for service in services.values():
        assert service["name"] and service["description"]


def test_dynamic_translation_keys_exist():
    """代码里动态拼出来的键(f-string),上面按字面量匹配的检查看不到,这里单独核对。"""
    source = (PACKAGE / "manager.py").read_text()
    assert 'translation_key=f"credentials_{status}"' in source, "manager.py 里的修复提示键写法变了,请同步更新这个测试"
    from_code = {"credentials_missing", "credentials_out_of_sync"}  # credentials.py 里 MISSING / OUT_OF_SYNC 对应的键
    for language in ("en", "zh-Hans"):
        issues = load(language)["issues"]
        assert from_code <= set(issues), language
        for key in from_code:
            step = issues[key]["fix_flow"]["step"]["confirm"]
            assert issues[key]["title"] and step["title"] and "{name}" in step["description"], (language, key)
            assert {"sync_failed", "not_loaded"} <= set(issues[key]["fix_flow"]["abort"])


def test_change_channel_error_keys_from_the_core_all_have_translations():
    """change.py 的每个错误码,manager 都会映射成翻译键;这些键必须存在。"""
    en = load("en")["exceptions"]
    for key in ("no_network", "not_leader", "already_on_channel", "change_failed", "change_not_switched", "invalid_parameter"):
        assert key in en, key

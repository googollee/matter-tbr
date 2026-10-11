# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""单元测试夹具:直接按路径加载 dataset.py,避免执行 thread_br/__init__.py(它依赖 Home Assistant)。"""

import importlib
from pathlib import Path
import sys
import types

import pytest

# 让 HA 能找到 custom_components.thread_br(仅集成测试用,在装了 HA 的环境里才会真正加载)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "thread_br"


def _load(name: str):
    """把 thread_br 目录登记为一个包但不执行它的 __init__.py(那里依赖 HA),再导入子模块。"""
    if "thread_br_pkg" not in sys.modules:
        package = types.ModuleType("thread_br_pkg")
        package.__path__ = [str(_PACKAGE)]
        sys.modules["thread_br_pkg"] = package
    return importlib.import_module(f"thread_br_pkg.{name}")


@pytest.fixture(scope="session")
def ds():
    return _load("dataset")


@pytest.fixture(scope="session")
def cr():
    return _load("create")


@pytest.fixture(scope="session")
def ch():
    return _load("channels")


@pytest.fixture(scope="session")
def chg():
    return _load("change")

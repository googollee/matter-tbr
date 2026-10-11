# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""Thread Border Router 集成的常量。"""

from __future__ import annotations

import logging

from .create import (
    ATTR_ACTIVE_DATASET_TIMESTAMP,
    ATTR_INTERFACE_ENABLED,
    THREAD_BR_MANAGEMENT_CLUSTER,
)

DOMAIN = "thread_br"
LOGGER = logging.getLogger(__package__)

DEFAULT_DEVICE_NAME = "Thread border router"

# 集成选项:用来帮自动选信道避开干扰
CONF_WIFI_CHANNELS = "wifi_channels"  # 附近 Wi-Fi 网络用的 2.4GHz 信道
CONF_AVOID_CHANNELS = "avoid_channels"  # 要避开的 Thread 信道

MATTER_DOMAIN = "matter"
# Matter 集成给节点设备注册的标识格式,我们的实体用同样的标识挂到同一个设备页
MATTER_ID_TYPE_DEVICE_ID = "deviceid"

# Thread BR 管理 cluster 的属性
ATTR_BORDER_ROUTER_NAME = 0

# Thread Network Diagnostics cluster(与边界路由器在同一个端点)
THREAD_NETWORK_DIAGNOSTICS_CLUSTER = 0x0035
ATTR_DIAG_CHANNEL = 0
ATTR_DIAG_ROUTING_ROLE = 1
ATTR_DIAG_NETWORK_NAME = 2
ATTR_DIAG_PAN_ID = 3
ATTR_DIAG_EXTENDED_PAN_ID = 4

# Thread Network Diagnostics 的 RoutingRole 枚举
ROUTING_ROLES = {
    0: "unspecified",
    1: "unassigned",
    2: "sleepy_end_device",
    3: "end_device",
    4: "reed",
    5: "router",
    6: "leader",
}

__all__ = [
    "CONF_AVOID_CHANNELS",
    "CONF_WIFI_CHANNELS",
    "ATTR_ACTIVE_DATASET_TIMESTAMP",
    "ATTR_BORDER_ROUTER_NAME",
    "ATTR_DIAG_CHANNEL",
    "ATTR_DIAG_EXTENDED_PAN_ID",
    "ATTR_DIAG_NETWORK_NAME",
    "ATTR_DIAG_PAN_ID",
    "ATTR_DIAG_ROUTING_ROLE",
    "ATTR_INTERFACE_ENABLED",
    "DEFAULT_DEVICE_NAME",
    "DOMAIN",
    "LOGGER",
    "MATTER_DOMAIN",
    "MATTER_ID_TYPE_DEVICE_ID",
    "ROUTING_ROLES",
    "THREAD_BR_MANAGEMENT_CLUSTER",
    "THREAD_NETWORK_DIAGNOSTICS_CLUSTER",
]

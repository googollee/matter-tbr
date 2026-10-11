# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""thread_br 的操作:创建网络、采纳网络、迁移信道、同步网络凭证。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .dataset import DEFAULT_DELAY_SECONDS, MAX_DELAY_SECONDS, MIN_DELAY_SECONDS

if TYPE_CHECKING:
    from .manager import ThreadBrManager

SERVICE_CREATE_NETWORK = "create_network"
SERVICE_ADOPT_NETWORK = "adopt_network"
SERVICE_CHANGE_CHANNEL = "change_channel"
SERVICE_SYNC_NETWORK = "sync_network"

ATTR_DEVICE_ID = "device_id"
ATTR_NETWORK_NAME = "network_name"
ATTR_CHANNEL = "channel"
ATTR_PASSPHRASE = "passphrase"
ATTR_DELAY = "delay"
ATTR_SOURCE_DEVICE = "source_device"

_DEVICE = {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])}

CREATE_NETWORK_SCHEMA = vol.Schema(
    {
        **_DEVICE,
        vol.Optional(ATTR_NETWORK_NAME): cv.string,
        vol.Optional(ATTR_CHANNEL): vol.All(vol.Coerce(int), vol.Range(min=11, max=26)),
        vol.Optional(ATTR_PASSPHRASE): cv.string,
    }
)
ADOPT_NETWORK_SCHEMA = vol.Schema(
    {
        **_DEVICE,
        vol.Optional(ATTR_SOURCE_DEVICE): cv.string,
        vol.Optional(ATTR_DELAY, default=DEFAULT_DELAY_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_DELAY_SECONDS, max=MAX_DELAY_SECONDS)
        ),
    }
)
CHANGE_CHANNEL_SCHEMA = vol.Schema(
    {
        **_DEVICE,
        vol.Optional(ATTR_CHANNEL): vol.All(vol.Coerce(int), vol.Range(min=11, max=26)),
        vol.Optional(ATTR_DELAY, default=DEFAULT_DELAY_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_DELAY_SECONDS, max=MAX_DELAY_SECONDS)
        ),
    }
)
SYNC_NETWORK_SCHEMA = vol.Schema(_DEVICE)


def _manager(hass: HomeAssistant) -> ThreadBrManager:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="integration_not_loaded")
    return entries[0].runtime_data.manager


def async_setup_services(hass: HomeAssistant) -> None:
    async def create_network(call: ServiceCall) -> None:
        manager = _manager(hass)
        for device_id in call.data[ATTR_DEVICE_ID]:
            await manager.async_create_network(
                manager.node_for_device(device_id),
                # 表单里留空的字段按"未填"处理
                network_name=call.data.get(ATTR_NETWORK_NAME) or None,
                channel=call.data.get(ATTR_CHANNEL),
                passphrase=call.data.get(ATTR_PASSPHRASE) or None,
            )

    async def adopt_network(call: ServiceCall) -> None:
        manager = _manager(hass)
        source = call.data.get(ATTR_SOURCE_DEVICE) or None  # 表单里留空按"未填"处理
        source_node_id = manager.node_for_device(source) if source else None
        for device_id in call.data[ATTR_DEVICE_ID]:
            await manager.async_adopt_network(
                manager.node_for_device(device_id), source_node_id=source_node_id, delay_seconds=call.data[ATTR_DELAY]
            )

    async def change_channel(call: ServiceCall) -> None:
        manager = _manager(hass)
        for device_id in call.data[ATTR_DEVICE_ID]:
            await manager.async_change_channel(
                manager.node_for_device(device_id),
                channel=call.data.get(ATTR_CHANNEL),
                delay_seconds=call.data[ATTR_DELAY],
            )

    async def sync_network(call: ServiceCall) -> None:
        manager = _manager(hass)
        for device_id in call.data[ATTR_DEVICE_ID]:
            await manager.async_sync_network(manager.node_for_device(device_id))

    hass.services.async_register(DOMAIN, SERVICE_CREATE_NETWORK, create_network, schema=CREATE_NETWORK_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ADOPT_NETWORK, adopt_network, schema=ADOPT_NETWORK_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHANGE_CHANNEL, change_channel, schema=CHANGE_CHANNEL_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SYNC_NETWORK, sync_network, schema=SYNC_NETWORK_SCHEMA)

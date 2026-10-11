# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""配置流:添加集成不需要参数;选项用来帮自动选信道避开干扰。"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import SelectOptionDict, SelectSelector, SelectSelectorConfig, SelectSelectorMode
import voluptuous as vol

from .channels import THREAD_CHANNELS, WIFI_CHANNELS
from .const import CONF_AVOID_CHANNELS, CONF_WIFI_CHANNELS, DOMAIN, MATTER_DOMAIN


def _selector(channels: tuple[int, ...]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=str(c), label=str(c)) for c in channels],
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


class ThreadBrConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ThreadBrOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self.hass.config_entries.async_loaded_entries(MATTER_DOMAIN):
            return self.async_abort(reason="matter_not_loaded")
        if user_input is not None:
            return self.async_create_entry(title="Thread Border Router", data={})
        return self.async_show_form(step_id="user")


class ThreadBrOptionsFlow(OptionsFlow):
    """创建网络时没有指定信道,就按这里的信息避开干扰来自动选。Zigbee(ZHA)的信道会自动读取。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_WIFI_CHANNELS: sorted(int(c) for c in user_input.get(CONF_WIFI_CHANNELS, [])),
                    CONF_AVOID_CHANNELS: sorted(int(c) for c in user_input.get(CONF_AVOID_CHANNELS, [])),
                }
            )

        def suggested(key: str) -> list[str]:
            return [str(c) for c in self.config_entry.options.get(key, [])]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_WIFI_CHANNELS, description={"suggested_value": suggested(CONF_WIFI_CHANNELS)}): _selector(
                        WIFI_CHANNELS
                    ),
                    vol.Optional(CONF_AVOID_CHANNELS, description={"suggested_value": suggested(CONF_AVOID_CHANNELS)}): _selector(
                        THREAD_CHANNELS
                    ),
                }
            ),
        )

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""修复流程:HA 里保存的 Thread 凭证与设备上的网络不一致时,一键从设备同步。"""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


class SyncCredentialsRepairFlow(ConfirmRepairFlow):
    """确认后从设备读回凭证并保存到 HA。"""

    def __init__(self, node_id: int) -> None:
        self._node_id = node_id

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="confirm")
        entries = self.hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return self.async_abort(reason="not_loaded")
        try:
            await entries[0].runtime_data.manager.async_sync_network(self._node_id)
        except HomeAssistantError as err:
            return self.async_abort(reason="sync_failed", description_placeholders={"reason": str(err)})
        return self.async_create_entry(data={})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None) -> RepairsFlow:
    assert data is not None and isinstance(data.get("node_id"), int)
    return SyncCredentialsRepairFlow(data["node_id"])

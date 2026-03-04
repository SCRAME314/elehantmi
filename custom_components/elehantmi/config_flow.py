"""Config flow for Elehant Meter Integration."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_TYPE,
    CONF_MANUAL_METERS,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_BT_ADAPTER,
    CONF_UNITS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_TIMEOUT,
    DEVICE_TYPE_GAS,
    DEVICE_TYPE_WATER,
    DOMAIN,
    UNIT_CUBIC_METERS,
    UNIT_LITERS,
)

_LOGGER = logging.getLogger(__name__)


class ElehantMeterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Elehant Meter."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        pass

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        return await self.async_step_manual_add()

    # ---------- РУЧНОЕ ДОБАВЛЕНИЕ ----------
    async def async_step_manual_add(self, user_input=None):
        """Handle manual addition of meter."""
        errors = {}
        
        if user_input is not None:
            serial = user_input[CONF_DEVICE_SERIAL]
            await self.async_set_unique_id(str(serial))
            self._abort_if_unique_id_configured()
            
            return self.async_create_entry(
                title=user_input[CONF_DEVICE_NAME],
                data={
                    CONF_MANUAL_METERS: [{
                        CONF_DEVICE_SERIAL: serial,
                        CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE],
                        CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                        CONF_UNITS: user_input[CONF_UNITS],
                    }]
                },
                options={
                    CONF_SELECTED_BT_ADAPTER: user_input.get(CONF_SELECTED_BT_ADAPTER, "hci0"),
                }
            )
        
        # Форма ручного ввода
        adapters = await self._get_bt_adapters()
        return self.async_show_form(
            step_id="manual_add",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_SERIAL): int,
                vol.Required(CONF_DEVICE_TYPE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": DEVICE_TYPE_GAS, "label": "Gas"},
                            {"value": DEVICE_TYPE_WATER, "label": "Water"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_UNITS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": UNIT_CUBIC_METERS, "label": "Cubic meters (m³)"},
                            {"value": UNIT_LITERS, "label": "Liters"},
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_DEVICE_NAME, default="Elehant Meter"): str,
                vol.Optional(CONF_SELECTED_BT_ADAPTER, default="hci0"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=adapters,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    # ---------- ВСПОМОГАТЕЛЬНОЕ ----------
    async def _get_bt_adapters(self):
        """Get list of available Bluetooth adapters."""
        adapters = [{"value": "hci0", "label": "Default (hci0)"}]
        try:
            import subprocess
            result = subprocess.run(["hciconfig"], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    if line.startswith("hci"):
                        adapter = line.split(":")[0]
                        if adapter not in ["hci0"]:
                            adapters.append({"value": adapter, "label": adapter})
        except Exception:
            pass
        return adapters

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Elehant Meter."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        adapters = await self._get_bt_adapters()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SELECTED_BT_ADAPTER,
                    default=self._config_entry.options.get(CONF_SELECTED_BT_ADAPTER, "hci0"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=adapters,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self._config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=300,
                        unit_of_measurement="seconds",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }),
        )

    async def _get_bt_adapters(self):
        """Get list of available Bluetooth adapters."""
        adapters = [{"value": "hci0", "label": "Default (hci0)"}]
        try:
            import subprocess
            result = subprocess.run(["hciconfig"], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    if line.startswith("hci"):
                        adapter = line.split(":")[0]
                        if adapter not in ["hci0"]:
                            adapters.append({"value": adapter, "label": adapter})
        except Exception:
            pass
        return adapters

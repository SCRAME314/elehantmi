"""Config flow for Elehant Meter Integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_NAME, CONF_UNIT_OF_MEASUREMENT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_BT_ADAPTER,
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_TYPE,
    CONF_LOCATION,
    CONF_MANUAL_METERS,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_BT_ADAPTER,
    CONF_UNITS,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_GAS,
    DEVICE_TYPE_WATER,
    DOMAIN,
    UNIT_CUBIC_METERS,
    UNIT_LITERS,
)
from .scanner import ElehantScanner, extract_serial_from_mac, get_device_type_from_mac

_LOGGER = logging.getLogger(__name__)


class ElehantMeterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Elehant Meter."""

    VERSION = 1
    
    def __init__(self):
        """Initialize the config flow."""
        self.discovered_devices = {}
        self.selected_devices = []
        self.scanner = None
        self.scan_task = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        # Простое меню без description_platform для совместимости
        return self.async_show_menu(
            step_id="user",
            menu_options=["manual_add", "auto_discover"],
        )  # ✅ Скобка на уровне return

    async def async_step_manual_add(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual addition of meter."""
        errors = {}
        
        if user_input is not None:
            serial = user_input[CONF_DEVICE_SERIAL]
            device_type = user_input[CONF_DEVICE_TYPE]
            
            # Check if device already configured
            await self.async_set_unique_id(str(serial))
            self._abort_if_unique_id_configured()
            
            # Save manual meter configuration
            return self.async_create_entry(
                title=user_input[CONF_DEVICE_NAME],
                data={
                    CONF_MANUAL_METERS: [{
                        CONF_DEVICE_SERIAL: serial,
                        CONF_DEVICE_TYPE: device_type,
                        CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                        CONF_UNITS: user_input[CONF_UNITS],
                        CONF_LOCATION: user_input.get(CONF_LOCATION, ""),
                    }]
                },
                options={
                    CONF_SELECTED_BT_ADAPTER: user_input.get(CONF_SELECTED_BT_ADAPTER, "hci0"),
                }
            )
        
        # Get available BT adapters
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
                vol.Optional(CONF_LOCATION): str,
                vol.Optional(CONF_SELECTED_BT_ADAPTER, default="hci0"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=adapters,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_auto_discover(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle auto-discovery of meters."""
        errors = {}
        
        if user_input is not None:
            # Stop scanning
            if self.scan_task:
                self.scan_task.cancel()
            if self.scanner:
                await self.scanner.stop()
            
            self.selected_devices = user_input.get("devices", [])
            if self.selected_devices:
                return await self.async_step_configure_devices()
            else:
                errors["base"] = "no_devices_selected"
        
        # Start scanning for devices
        if not self.scanner:
            self.scanner = ElehantScanner(self.hass)
            adapter = user_input.get(CONF_SELECTED_BT_ADAPTER, "hci0") if user_input else "hci0"
            await self.scanner.start(adapter=adapter)
            
            # Run scan for 10 seconds
            self.scan_task = asyncio.create_task(self._scan_for_devices())
        
        # Get discovered devices
        discovered = []
        for mac, data in self.discovered_devices.items():
            serial = data["serial"]
            device_type = data["device_type"]
            discovered.append({
                "value": mac,
                "label": f"{serial} - {'Gas' if device_type == DEVICE_TYPE_GAS else 'Water'} ({mac})",
            })
        
        # Get available BT adapters
        adapters = await self._get_bt_adapters()
        
        return self.async_show_form(
            step_id="auto_discover",
            data_schema=vol.Schema({
                vol.Required("devices"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=discovered,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_SELECTED_BT_ADAPTER, default="hci0"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=adapters,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_configure_devices(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure discovered devices."""
        errors = {}
        
        if user_input is not None:
            meters = []
            for mac in self.selected_devices:
                device_info = self.discovered_devices[mac]
                serial = device_info["serial"]
                
                # Check if device already configured
                await self.async_set_unique_id(str(serial))
                if self._async_current_entries():
                    continue
                
                meters.append({
                    CONF_DEVICE_SERIAL: serial,
                    CONF_DEVICE_TYPE: device_info["device_type"],
                    CONF_DEVICE_NAME: user_input.get(f"name_{serial}", 
                                                     f"Elehant {'Gas' if device_info['device_type'] == DEVICE_TYPE_GAS else 'Water'} {serial}"),
                    CONF_UNITS: user_input.get(f"units_{serial}", UNIT_CUBIC_METERS),
                    CONF_LOCATION: user_input.get(f"location_{serial}", ""),
                })
            
            if meters:
                return self.async_create_entry(
                    title="Elehant Meters",
                    data={
                        CONF_MANUAL_METERS: meters,
                    },
                    options={
                        CONF_SELECTED_BT_ADAPTER: user_input.get(CONF_SELECTED_BT_ADAPTER, "hci0"),
                    }
                )
            else:
                errors["base"] = "all_devices_configured"
        
        # Build schema for device configuration
        schema = {}
        for mac in self.selected_devices:
            device_info = self.discovered_devices[mac]
            serial = device_info["serial"]
            device_type = device_info["device_type"]
            
            schema[vol.Required(f"name_{serial}", 
                               default=f"Elehant {'Gas' if device_type == DEVICE_TYPE_GAS else 'Water'} {serial}")] = str
            schema[vol.Optional(f"location_{serial}", default="")] = str
            schema[vol.Required(f"units_{serial}", default=UNIT_CUBIC_METERS)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": UNIT_CUBIC_METERS, "label": "Cubic meters (m³)"},
                        {"value": UNIT_LITERS, "label": "Liters"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        
        return self.async_show_form(
            step_id="configure_devices",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def _scan_for_devices(self):
        """Scan for devices for 10 seconds."""
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            if self.scanner:
                self.discovered_devices = self.scanner.discovered_devices
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_configure(flow_id=self.flow_id)
            )

    async def _get_bt_adapters(self):
        """Get list of available Bluetooth adapters."""
        adapters = [{"value": "hci0", "label": "Default (hci0)"}]
        
        try:
            # Try to get list of adapters from system
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
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Elehant Meter."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get available BT adapters
        adapters = await self._get_bt_adapters()
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SELECTED_BT_ADAPTER,
                    default=self.config_entry.options.get(CONF_SELECTED_BT_ADAPTER, "hci0"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=adapters,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
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
            # Try to get list of adapters from system
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

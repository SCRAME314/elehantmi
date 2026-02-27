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
    CONF_LOCATION,
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
        self.discovered_devices = []
        self.scan_task: asyncio.Task | None = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["manual_add", "auto_discover"],
        )

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
                        CONF_LOCATION: user_input.get(CONF_LOCATION, ""),
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

async def async_step_auto_discover(self, user_input=None):
    if user_input is None:
        # запускаем сканирование
        self.scan_task = asyncio.create_task(
            self._scan_and_gather(self.hass.data[DOMAIN]["scanner"], DEFAULT_SCAN_TIMEOUT)
        )
        return self.async_show_progress(
            step_id="auto_discover_progress",
            progress_action="scanning",
            progress_task=self.scan_task,
        )
    
    # Когда сканирование завершено, переходим на промежуточный шаг
    return await self.async_step_auto_discover_done()

    async def async_step_auto_discover_progress(self, user_input=None):
        """Step to show progress of scanning."""
        # Этот шаг автоматически управляется HA
        # Просто передаем управление дальше
        return await self.async_step_auto_discover_done()

    async def async_step_auto_discover_done(self, user_input=None):
        """Handle completion of auto discovery."""
        if not self.discovered_devices:
            return self.async_abort(reason="no_devices_found")
        return await self.async_step_select_devices()

    async def _scan_and_gather(self, scanner, timeout: int):
        """Wait for scan to complete and gather devices."""
        await asyncio.sleep(timeout)
        
        # Собираем устройства из истории сканера, виденные за последние 24 часа
        recent = scanner.get_recent_devices(hours=24)
        
        # Фильтруем уже настроенные
        self.discovered_devices = []
        for dev in recent:
            unique_id = str(dev["serial"])
            # Проверяем, не настроено ли уже
            if self._async_current_ids().get(unique_id):
                continue
            self.discovered_devices.append(dev)
        
        # Завершаем шаг прогресса (HA сам вызовет следующий шаг)
        # Ничего не делаем, просто возвращаемся

    async def async_step_select_devices(self, user_input=None):
        """Let user select devices from the list."""
        if user_input is not None:
            selected_macs = user_input.get("devices", [])
            if not selected_macs:
                return self.async_abort(reason="no_devices_selected")
            
            # Переходим к конфигурации выбранных устройств
            self.selected_devices = [
                dev for dev in self.discovered_devices if dev["mac"] in selected_macs
            ]
            return await self.async_step_configure_devices()
        
        # Строим список опций для выбора
        options = []
        for dev in self.discovered_devices:
            last_seen_str = time.strftime(
                "%H:%M %d.%m", time.localtime(dev["last_seen"])
            )
            label = f"{dev['device_type'].upper()}: {dev['serial']} (модель {dev['model']}, RSSI:{dev['best_rssi']}) - last seen: {last_seen_str}"
            options.append({"value": dev["mac"], "label": label})
        
        return self.async_show_form(
            step_id="select_devices",
            data_schema=vol.Schema({
                vol.Required("devices"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }),
        )

    async def async_step_configure_devices(self, user_input=None):
        """Configure each selected device."""
        errors = {}
        
        if user_input is not None:
            meters = []
            for dev in self.selected_devices:
                serial = dev["serial"]
                # Еще раз проверяем, не добавили ли параллельно
                if self._async_current_ids().get(str(serial)):
                    continue
                
                meters.append({
                    CONF_DEVICE_SERIAL: serial,
                    CONF_DEVICE_TYPE: dev["device_type"],
                    CONF_DEVICE_NAME: user_input.get(
                        f"name_{serial}",
                        f"Elehant {dev['device_type'].capitalize()} {serial}"
                    ),
                    CONF_UNITS: user_input.get(f"units_{serial}", UNIT_CUBIC_METERS),
                    CONF_LOCATION: user_input.get(f"location_{serial}", ""),
                })
            
            if meters:
                return self.async_create_entry(
                    title="Elehant Meters",
                    data={CONF_MANUAL_METERS: meters},
                    options={},
                )
            else:
                errors["base"] = "all_devices_configured"
        
        # Строим схему с полями для каждого устройства
        schema = {}
        for dev in self.selected_devices:
            serial = dev["serial"]
            default_name = f"Elehant {dev['device_type'].capitalize()} {serial}"
            schema[vol.Required(f"name_{serial}", default=default_name)] = str
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

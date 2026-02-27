"""Elehant Meter Integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_TYPE,
    CONF_MANUAL_METERS,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_BT_ADAPTER,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_GAS,
    DEVICE_TYPE_WATER,
    DOMAIN,
    SIGNAL_NEW_DATA,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .scanner import ElehantScanner, get_device_type_from_mac

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Elehant Meter from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    # Create storage for device configurations
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    
    # Initialize scanner if not already running
    if "scanner" not in hass.data[DOMAIN]:
        scanner = ElehantScanner(hass)
        hass.data[DOMAIN]["scanner"] = scanner

        # СОЗДАЕМ УСТРОЙСТВО ДЛЯ СКАНЕРА
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "scanner")},
            name="Elehant BLE Scanner",
            manufacturer="Elehant",
            model="Bluetooth Scanner",
            sw_version="1.0",
        )
    
        
        # Start scanning with configured adapter
        bt_adapter = entry.options.get(CONF_SELECTED_BT_ADAPTER, "hci0")
        await scanner.start(adapter=bt_adapter)
        
        # Store scanner reference for cleanup
        entry.async_on_unload(scanner.stop())
    
    # Create device trackers for each configured meter
    meters = entry.data.get(CONF_MANUAL_METERS, [])
    if isinstance(meters, dict):
        meters = [meters]
    
    # Register devices in device registry
    device_registry = dr.async_get(hass)
    
    for meter_config in meters:
        serial = meter_config[CONF_DEVICE_SERIAL]
        device_type = meter_config[CONF_DEVICE_TYPE]
        device_name = meter_config[CONF_DEVICE_NAME]
        
        # Create device in registry
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, str(serial))},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.0",
        )
        
        # Store meter config for sensors
        hass.data[DOMAIN][f"meter_{serial}"] = meter_config
    
    # Forward setup to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Subscribe to scanner updates
    entry.async_on_unload(
        hass.data[DOMAIN]["scanner"].async_add_listener(
            lambda data: async_dispatcher_send(hass, SIGNAL_NEW_DATA, data)
        )
    )
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Stop scanner if this is the last entry
        if len(hass.config_entries.async_entries(DOMAIN)) == 1:
            scanner = hass.data[DOMAIN].get("scanner")
            if scanner:
                await scanner.stop()
            hass.data[DOMAIN].pop("scanner", None)
        
        # Remove device configurations
        for key in list(hass.data[DOMAIN].keys()):
            if key.startswith("meter_"):
                hass.data[DOMAIN].pop(key, None)
    
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)
    
    if config_entry.version == 1:
        # Migration logic for future versions
        config_entry.version = 2
    
    return True

"""Elehant Meter Integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_TYPE,
    CONF_MANUAL_METERS,
    DEVICE_TYPE_GAS,
    DOMAIN,
)
from .scanner import ElehantHistoryScanner

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Elehant Meter from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    # Создаем глобальный сканер при первом запуске
    if "scanner" not in hass.data[DOMAIN]:
        scanner = ElehantHistoryScanner(hass)
        hass.data[DOMAIN]["scanner"] = scanner
        await scanner.start()
        entry.async_on_unload(scanner.stop)
        _LOGGER.info("Global Elehant history scanner created")
    
    # Регистрируем устройства из конфига
    meters = entry.data.get(CONF_MANUAL_METERS, [])
    if isinstance(meters, dict):
        meters = [meters]
    
    device_registry = dr.async_get(hass)
    for meter_config in meters:
        serial = meter_config[CONF_DEVICE_SERIAL]
        device_type = meter_config[CONF_DEVICE_TYPE]
        device_name = meter_config[CONF_DEVICE_NAME]
        # Composite key: serial + device_type to distinguish water/gas meters
        device_key = f"{serial}_{device_type}"
        
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device_key)},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.5.0",
        )
        hass.data[DOMAIN][f"meter_{device_key}"] = meter_config
        _LOGGER.debug(f"Registered meter {serial} ({device_type})")
    
    # Запускаем сенсоры только для счётчиков из этого config entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Удаляем данные только для счётчиков из этого config entry
        meters = entry.data.get(CONF_MANUAL_METERS, [])
        if isinstance(meters, dict):
            meters = [meters]
        for meter_config in meters:
            device_key = f"{meter_config[CONF_DEVICE_SERIAL]}_{meter_config[CONF_DEVICE_TYPE]}"
            for prefix in ("meter_", "coordinator_", "entity_"):
                key = f"{prefix}{device_key}"
                hass.data[DOMAIN].pop(key, None)
                # Also clean old-style keys (without device_type) for migration
                old_key = f"{prefix}{meter_config[CONF_DEVICE_SERIAL]}"
                hass.data[DOMAIN].pop(old_key, None)
        
        # Сканер НЕ останавливаем, если есть другие активные entry
        # Он остановится, когда удалится последняя entry (через callback в async_setup_entry)
    
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)
    if config_entry.version == 1:
        config_entry.version = 2
    return True

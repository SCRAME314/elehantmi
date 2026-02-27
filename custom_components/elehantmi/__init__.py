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
        
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, str(serial))},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.0",
        )
        hass.data[DOMAIN][f"meter_{serial}"] = meter_config
        _LOGGER.debug(f"Registered meter {serial}")
    
    # Запускаем сенсоры
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Удаляем данные конкретного счетчика
        for key in list(hass.data[DOMAIN].keys()):
            if key.startswith("meter_") or key.startswith("coordinator_"):
                hass.data[DOMAIN].pop(key, None)
        
        # Сканер НЕ останавливаем, если есть другие активные entry
        # Он остановится, когда удалится последняя entry (через callback в async_setup_entry)
    
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)
    if config_entry.version == 1:
        config_entry.version = 2
    return True

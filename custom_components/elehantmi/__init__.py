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
        
        # Используем комбинацию serial и device_type для уникальной идентификации устройства
        # Это предотвращает дублирование устройств когда один серийный номер используется для разных типов (газ/вода)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{serial}_{device_type}")},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.0",
        )
        # Сохраняем meter_config с привязкой к entry_id
        meter_key = f"meter_{serial}_{device_type}"
        hass.data[DOMAIN][meter_key] = {
            **meter_config,
            "_entry_id": entry.entry_id,
        }
        _LOGGER.debug(f"Registered meter {serial} ({device_type}) for entry {entry.entry_id}")
    
    # Запускаем сенсоры
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Удаляем данные конкретного счетчика для этой entry
        for key in list(hass.data[DOMAIN].keys()):
            if key.startswith("meter_"):
                meter_data = hass.data[DOMAIN][key]
                if meter_data.get("_entry_id") == entry.entry_id:
                    hass.data[DOMAIN].pop(key, None)
        
        # Очищаем set добавленных сущностей для этой entry
        # Это позволяет корректно пересоздать сущности при повторной загрузке
        if "added_entities" in hass.data[DOMAIN]:
            # Находим все unique_id, которые принадлежат этой entry
            entry_unique_ids = set()
            for key in list(hass.data[DOMAIN].keys()):
                if key.startswith("meter_"):
                    meter_data = hass.data[DOMAIN][key]
                    if meter_data.get("_entry_id") == entry.entry_id:
                        serial = meter_data[CONF_DEVICE_SERIAL]
                        device_type = meter_data[CONF_DEVICE_TYPE]
                        # Добавляем все возможные unique_id для этого meter
                        for sensor_type in ["meter", "temperature", "battery"]:
                            entry_unique_ids.add(f"{serial}_{device_type}_{sensor_type}")
            
            # Удаляем unique_id этой entry из added_entities
            hass.data[DOMAIN]["added_entities"] -= entry_unique_ids
        
        # Удаляем координаторы, которые больше не используются
        for key in list(hass.data[DOMAIN].keys()):
            if key.startswith("coordinator_"):
                serial = key.replace("coordinator_", "")
                # Проверяем, есть ли еще активные meter_ с этим serial
                if not any(k.startswith(f"meter_{serial}_") for k in hass.data[DOMAIN].keys()):
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

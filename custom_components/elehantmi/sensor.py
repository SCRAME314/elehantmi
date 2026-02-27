"""Sensor platform for Elehant Meter Integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_TYPE,
    CONF_LOCATION,
    CONF_UNITS,
    DEVICE_TYPE_GAS,
    DEVICE_TYPE_WATER,
    DOMAIN,
    SENSOR_TYPE_BATTERY,
    SENSOR_TYPE_METER,
    SENSOR_TYPE_TEMPERATURE,
    SIGNAL_NEW_DATA,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
    UNIT_CUBIC_METERS,
    UNIT_LITERS,
)
from .coordinator import ElehantDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Elehant sensors based on a config entry."""
    entities = []
    
    # Важно: итерируемся по копии ключей, чтобы избежать ошибки изменения словаря
    for key in list(hass.data[DOMAIN].keys()):
        if not key.startswith("meter_"):
            continue
        
        meter_config = hass.data[DOMAIN][key]
        serial = meter_config[CONF_DEVICE_SERIAL]
        device_type = meter_config[CONF_DEVICE_TYPE]
        device_name = meter_config[CONF_DEVICE_NAME]
        units = meter_config[CONF_UNITS]
        location = meter_config.get(CONF_LOCATION, "")
        
        # Create coordinator for this meter if not exists
        coord_key = f"coordinator_{serial}"
        if coord_key not in hass.data[DOMAIN]:
            coordinator = ElehantDataUpdateCoordinator(hass, serial)
            hass.data[DOMAIN][coord_key] = coordinator
        else:
            coordinator = hass.data[DOMAIN][coord_key]
        
        entities.extend([
            ElehantMeterSensor(coordinator, serial, device_type, device_name, units, location),
            ElehantTemperatureSensor(coordinator, serial, device_type, device_name, location),
            ElehantBatterySensor(coordinator, serial, device_type, device_name, location),
        ])
        _LOGGER.debug(f"Created sensors for meter {serial}")
    
    if entities:
        async_add_entities(entities)


class ElehantBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Elehant sensors."""

    def __init__(
        self,
        coordinator: ElehantDataUpdateCoordinator,
        serial: int,
        device_type: str,
        device_name: str,
        sensor_type: str,
        location: str = "",
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._serial = serial
        self._device_type = device_type
        self._device_name = device_name
        self._sensor_type = sensor_type
        self._location = location
        self._attr_unique_id = f"{serial}_{sensor_type}"
        self._attr_should_poll = False
        

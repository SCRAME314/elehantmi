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
    DEVICE_CLASS_BATTERY,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_TYPE_GAS,
    DEVICE_TYPE_WATER,
    DOMAIN,
    SENSOR_TYPE_BATTERY,
    SENSOR_TYPE_METER,
    SENSOR_TYPE_TEMPERATURE,
    SIGNAL_NEW_DATA,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
    UNIT_BATTERY,
    UNIT_CUBIC_METERS,
    UNIT_LITERS,
    UNIT_TEMPERATURE,
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
    
    # Get all configured meters
    for meter_serial in list(hass.data[DOMAIN].keys()):
        if not meter_serial.startswith("meter_"):
            continue
        meter_config = hass.data[DOMAIN][meter_serial]
        
        serial = meter_config[CONF_DEVICE_SERIAL]
        device_type = meter_config[CONF_DEVICE_TYPE]
        device_name = meter_config[CONF_DEVICE_NAME]
        units = meter_config[CONF_UNITS]
        location = meter_config.get(CONF_LOCATION, "")
        
        # Create coordinator for this meter
        coordinator = ElehantDataUpdateCoordinator(hass, serial)
        hass.data[DOMAIN][f"coordinator_{serial}"] = coordinator
        
        # Create sensors
        entities.extend([
            ElehantMeterSensor(coordinator, serial, device_type, device_name, units, location),
            ElehantTemperatureSensor(coordinator, serial, device_type, device_name, location),
            ElehantBatterySensor(coordinator, serial, device_type, device_name, location),
        ])
    
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
        
        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(serial))},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.0",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data:
            self._attr_native_value = self._get_state_from_data(self.coordinator.data)
        self.async_write_ha_state()

    def _get_state_from_data(self, data: dict) -> Any:
        """Extract state from coordinator data."""
        raise NotImplementedError


class ElehantMeterSensor(ElehantBaseSensor):
    """Sensor for meter readings."""

    def __init__(
        self,
        coordinator: ElehantDataUpdateCoordinator,
        serial: int,
        device_type: str,
        device_name: str,
        units: str,
        location: str = "",
    ) -> None:
        """Initialize meter sensor."""
        super().__init__(coordinator, serial, device_type, device_name, SENSOR_TYPE_METER, location)
        self._units = units
        
        # Set sensor attributes
        self._attr_name = f"{device_name} Reading"
        self._attr_state_class = STATE_CLASS_TOTAL_INCREASING
        
        if device_type == DEVICE_TYPE_GAS:
            self._attr_device_class = SensorDeviceClass.GAS
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:  # Water
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = (
                UnitOfVolume.CUBIC_METERS if units == UNIT_CUBIC_METERS 
                else UnitOfVolume.LITERS
            )

    def _get_state_from_data(self, data: dict) -> float | None:
        """Get meter reading from data."""
        if "value" not in data:
            return None
        
        raw_value = data["value"]  # This is already in tenths (0.1 units)
        
        if self._device_type == DEVICE_TYPE_GAS:
            # Gas is always in m³
            return raw_value / 10  # Convert from 0.1 m³ to m³
        else:  # Water
            if self._units == UNIT_CUBIC_METERS:
                return raw_value / 1000  # Convert from liters to m³
            else:
                return raw_value / 10  # Convert from 0.1 L to L


class ElehantTemperatureSensor(ElehantBaseSensor):
    """Sensor for temperature readings."""

    def __init__(
        self,
        coordinator: ElehantDataUpdateCoordinator,
        serial: int,
        device_type: str,
        device_name: str,
        location: str = "",
    ) -> None:
        """Initialize temperature sensor."""
        super().__init__(coordinator, serial, device_type, device_name, SENSOR_TYPE_TEMPERATURE, location)
        
        self._attr_name = f"{device_name} Temperature"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_state_class = STATE_CLASS_MEASUREMENT

    def _get_state_from_data(self, data: dict) -> float | None:
        """Get temperature from data."""
        return data.get("temperature")


class ElehantBatterySensor(ElehantBaseSensor):
    """Sensor for battery level."""

    def __init__(
        self,
        coordinator: ElehantDataUpdateCoordinator,
        serial: int,
        device_type: str,
        device_name: str,
        location: str = "",
    ) -> None:
        """Initialize battery sensor."""
        super().__init__(coordinator, serial, device_type, device_name, SENSOR_TYPE_BATTERY, location)
        
        self._attr_name = f"{device_name} Battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = STATE_CLASS_MEASUREMENT

    def _get_state_from_data(self, data: dict) -> int:
        """Get battery level from data."""
        # Battery not yet implemented, return 100%
        return 100

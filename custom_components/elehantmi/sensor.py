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
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_TYPE,
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
        location = ""
        
        # Create coordinator for this meter if not exists
        coord_key = f"coordinator_{serial}"
        if coord_key not in hass.data[DOMAIN]:
            coordinator = ElehantDataUpdateCoordinator(hass, serial)
            hass.data[DOMAIN][coord_key] = coordinator
        else:
            coordinator = hass.data[DOMAIN][coord_key]
        
        # Check if entities with these unique_ids already exist to prevent duplication
        existing_unique_ids = set()
        for entity_key in list(hass.data[DOMAIN].keys()):
            if entity_key.startswith("entity_"):
                existing_unique_ids.add(hass.data[DOMAIN][entity_key])
        
        # Create sensors with unique IDs that include device type
        sensor_configs = [
            (ElehantMeterSensor, f"{serial}_{device_type}_{SENSOR_TYPE_METER}"),
            (ElehantTemperatureSensor, f"{serial}_{device_type}_{SENSOR_TYPE_TEMPERATURE}"),
            (ElehantBatterySensor, f"{serial}_{device_type}_{SENSOR_TYPE_BATTERY}"),
        ]
        
        for sensor_class, unique_id in sensor_configs:
            if unique_id not in existing_unique_ids:
                if sensor_class == ElehantMeterSensor:
                    entity = sensor_class(coordinator, serial, device_type, device_name, units, location)
                else:
                    entity = sensor_class(coordinator, serial, device_type, device_name, location)
                entities.append(entity)
                hass.data[DOMAIN][f"entity_{unique_id}"] = unique_id
                _LOGGER.debug(f"Created sensor with unique_id: {unique_id}")
            else:
                _LOGGER.debug(f"Skipping duplicate sensor with unique_id: {unique_id}")
    
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
        # Unique ID now includes device_type to ensure uniqueness across different meter types
        self._attr_unique_id = f"{serial}_{device_type}_{sensor_type}"
        self._attr_should_poll = False
        
        # Set device info (via_device убрано, чтобы не было предупреждений)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(serial))},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.0",
        )
        
        # Store last valid value for recovery from invalid states
        self._last_valid_value = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data:
            value = self._get_state_from_data(self.coordinator.data)
            
            # Validate the value before setting it
            if value is not None:
                try:
                    # Try to convert to float - this will catch 'unknown' strings and other invalid values
                    numeric_value = float(value)
                    self._attr_native_value = numeric_value
                    self._last_valid_value = numeric_value
                except (ValueError, TypeError):
                    # Value cannot be converted to float (e.g., 'unknown' string)
                    # Don't set the value, keep the last valid one or leave as None
                    _LOGGER.debug(
                        f"Invalid value '{value}' ({type(value).__name__}) for sensor {self._attr_unique_id}, "
                        f"keeping last valid value: {self._last_valid_value}"
                    )
                    # Keep the last valid value if available, otherwise state will be None/unavailable
                    if self._last_valid_value is not None:
                        self._attr_native_value = self._last_valid_value
                    # If no last valid value, don't call async_write_ha_state() to avoid setting invalid state
                    else:
                        return  # Skip state update entirely
            # If value is None, we don't update - sensor stays in its current state
        
        self.async_write_ha_state()

    def _get_state_from_data(self, data: dict) -> Any:
        raise NotImplementedError


class ElehantMeterSensor(ElehantBaseSensor, RestoreEntity):
    """Sensor for meter readings."""

    def __init__(self, coordinator, serial, device_type, device_name, units, location=""):
        super().__init__(coordinator, serial, device_type, device_name, SENSOR_TYPE_METER, location)
        self._units = units
        self._attr_name = f"{device_name} Reading"
        self._attr_state_class = STATE_CLASS_TOTAL_INCREASING
        
        if device_type == DEVICE_TYPE_GAS:
            self._attr_device_class = SensorDeviceClass.GAS
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = (
                UnitOfVolume.CUBIC_METERS if units == UNIT_CUBIC_METERS else UnitOfVolume.LITERS
            )

    async def async_added_to_hass(self) -> None:
        """Restore last known state."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                # Restore only if the last state was a valid number
                restored_value = float(last_state.state)
                self._attr_native_value = restored_value
                self._last_valid_value = restored_value
            except (ValueError, TypeError):
                # Last state was not numeric (e.g., 'unknown'), don't restore it
                _LOGGER.debug(
                    f"Last state '{last_state.state}' for {self._attr_unique_id} was not numeric, skipping restoration"
                )

    def _get_state_from_data(self, data: dict) -> float | None:
        if "value" not in data:
            return None
        raw_value = data["value"]
        
        # Validate raw_value is numeric before processing
        if raw_value is None or not isinstance(raw_value, (int, float)):
            try:
                raw_value = int(raw_value)
            except (ValueError, TypeError):
                _LOGGER.debug(f"Invalid raw_value '{raw_value}' ({type(raw_value).__name__}), returning None")
                return None
        
        # According to packet structure:
        # Raw value represents 0.1 liters (stored as integer)
        # So for liters we divide by 10 (raw_value / 10)
        # For cubic meters we divide by 10000 (raw_value / 10000) since 1 m³ = 1000 L and value is in 0.1 L
        if self._device_type == DEVICE_TYPE_GAS:
            if self._attr_native_unit_of_measurement == UnitOfVolume.CUBIC_METERS:
                # For gas in cubic meters: raw_value / 10000 (raw_value * 0.0001)
                return raw_value / 10000
            else:
                # For gas in liters: raw_value / 10 (since raw value represents 0.1 liters)
                return raw_value / 10
        else:  # Water meter
            if self._attr_native_unit_of_measurement == UnitOfVolume.CUBIC_METERS:
                # For water in cubic meters: raw_value / 10000 (raw_value * 0.0001)
                return raw_value / 10000
            else:
                # For water in liters: raw_value / 10 (since raw value represents 0.1 liters)
                return raw_value / 10


class ElehantTemperatureSensor(ElehantBaseSensor):
    """Sensor for temperature readings."""

    def __init__(self, coordinator, serial, device_type, device_name, location=""):
        super().__init__(coordinator, serial, device_type, device_name, SENSOR_TYPE_TEMPERATURE, location)
        self._attr_name = f"{device_name} Temperature"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_state_class = STATE_CLASS_MEASUREMENT

    def _get_state_from_data(self, data: dict) -> float | None:
        temp = data.get("temperature")
        if temp is None:
            return None
        # Validate temperature is numeric
        try:
            return float(temp)
        except (ValueError, TypeError):
            _LOGGER.debug(f"Invalid temperature value '{temp}' ({type(temp).__name__}), returning None")
            return None


class ElehantBatterySensor(ElehantBaseSensor):
    """Sensor for battery level."""

    def __init__(self, coordinator, serial, device_type, device_name, location=""):
        super().__init__(coordinator, serial, device_type, device_name, SENSOR_TYPE_BATTERY, location)
        self._attr_name = f"{device_name} Battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = STATE_CLASS_MEASUREMENT

    def _get_state_from_data(self, data: dict) -> int | None:
        # Return None to let the base class handle validation and last valid value
        return 100  # Placeholder - always valid numeric value

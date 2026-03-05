"""Counter platform for Elehant Meter Integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.counter import CounterEntity
from homeassistant.config_entries import ConfigEntry
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
    SENSOR_TYPE_METER,
    SIGNAL_NEW_DATA,
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
    """Set up Elehant counters based on a config entry."""
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
        
        # Create coordinator for this meter if not exists
        coord_key = f"coordinator_{serial}"
        if coord_key not in hass.data[DOMAIN]:
            coordinator = ElehantDataUpdateCoordinator(hass, serial)
            hass.data[DOMAIN][coord_key] = coordinator
        else:
            coordinator = hass.data[DOMAIN][coord_key]
        
        entities.append(
            ElehantMeterCounter(coordinator, serial, device_type, device_name, units)
        )
        _LOGGER.debug(f"Created counter for meter {serial}")
    
    if entities:
        async_add_entities(entities)


class ElehantMeterCounter(CoordinatorEntity, CounterEntity, RestoreEntity):
    """Counter for meter readings with restoration capability."""

    def __init__(self, coordinator, serial, device_type, device_name, units):
        """Initialize the counter."""
        CoordinatorEntity.__init__(self, coordinator)
        self.coordinator = coordinator
        self._serial = serial
        self._device_type = device_type
        self._device_name = device_name
        self._units = units
        self._attr_unique_id = f"{serial}_reading"
        self._attr_name = f"{device_name} Reading"
        self._attr_should_poll = False
        
        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(serial))},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.0",
        )

        # Установка единиц измерения в зависимости от типа устройства
        if device_type == DEVICE_TYPE_GAS:
            self._attr_unit_of_measurement = UNIT_CUBIC_METERS
        else:  # Water meter
            self._attr_unit_of_measurement = UNIT_CUBIC_METERS if units == UNIT_CUBIC_METERS else UNIT_LITERS

        # Инициализация начального значения
        self._current_value = 0.0

    async def async_added_to_hass(self) -> None:
        """Restore last reported value if available."""
        await super().async_added_to_hass()
        
        # Попытка восстановления последнего значения
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in ["unknown", "unavailable", "none"]:
                try:
                    self._current_value = float(last_state.state)
                    _LOGGER.debug(f"Restored counter value {self._current_value} for {self._serial}")
                except ValueError:
                    _LOGGER.warning(f"Could not restore counter state for {self._serial}: {last_state.state}")
        
        # Подписка на обновления координатора
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data:
            new_value = self._get_state_from_data(self.coordinator.data)
            if new_value is not None and new_value != self._current_value:
                self._current_value = new_value
        self.async_write_ha_state()

    def _get_state_from_data(self, data: dict) -> float | None:
        """Extract the reading value from the data."""
        if "value" not in data:
            return None
        raw_value = data["value"]
        
        # According to packet structure:
        # Raw value represents 0.1 liters (stored as integer)
        # So for liters we divide by 10 (raw_value / 10)
        # For cubic meters we divide by 10000 (raw_value / 10000) since 1 m³ = 1000 L and value is in 0.1 L
        if self._device_type == DEVICE_TYPE_GAS:
            if self._attr_unit_of_measurement == UNIT_CUBIC_METERS:
                # For gas in cubic meters: raw_value / 10000 (raw_value * 0.0001)
                return raw_value / 10000
            else:
                # For gas in liters: raw_value / 10 (since raw value represents 0.1 liters)
                return raw_value / 10
        else:  # Water meter
            if self._attr_unit_of_measurement == UNIT_CUBIC_METERS:
                # For water in cubic meters: raw_value / 10000 (raw_value * 0.0001)
                return raw_value / 10000
            else:
                # For water in liters: raw_value / 10 (since raw value represents 0.1 liters)
                return raw_value / 10

    @property
    def native_value(self) -> int | float:
        """Return the current value of the counter."""
        return self._current_value

    def increment(self) -> None:
        """Increment the counter."""
        # Для этого типа счетчика увеличение не используется, так как значение берется из данных устройства
        pass

    def decrement(self) -> None:
        """Decrement the counter."""
        # Для этого типа счетчика уменьшение не используется, так как значение берется из данных устройства
        pass

    def reset(self) -> None:
        """Reset the counter to zero."""
        # Для этого типа счетчика сброс не используется, так как значение берется из данных устройства
        pass
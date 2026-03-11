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
    from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
    
    entities = []
    seen_unique_ids: set[str] = set()
    
    # Получаем реестр сущностей для проверки существующих unique_id
    entity_registry = async_get_entity_registry(hass)
    
    # Получаем список serial+device_type только для текущей config_entry
    # Это предотвращает создание дублирующихся сенсоров при наличии нескольких entry
    meters_to_process: list[tuple[int, str, dict]] = []
    for key in list(hass.data[DOMAIN].keys()):
        if not key.startswith("meter_"):
            continue
        
        meter_config = hass.data[DOMAIN][key]
        # Проверяем, что meter принадлежит текущей config_entry
        if meter_config.get("_entry_id") != config_entry.entry_id:
            _LOGGER.debug(f"Skipping meter {key} - belongs to different entry: {meter_config.get('_entry_id')}")
            continue
        
        serial = meter_config[CONF_DEVICE_SERIAL]
        device_type = meter_config[CONF_DEVICE_TYPE]
        meters_to_process.append((serial, device_type, meter_config))
        _LOGGER.debug(f"Will process meter {serial} ({device_type}) for entry {config_entry.entry_id}")
    
    # Создаем сенсоры только для meter'ов текущей entry
    for serial, device_type, meter_config in meters_to_process:
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
        
        # Создаем список сенсоров для этого счетчика
        sensor_classes: list[tuple[type, tuple]] = [
            (ElehantMeterSensor, (coordinator, serial, device_type, device_name, units, location)),
            (ElehantTemperatureSensor, (coordinator, serial, device_type, device_name, location)),
            (ElehantBatterySensor, (coordinator, serial, device_type, device_name, location)),
        ]
        
        for sensor_class, args in sensor_classes:
            # Создаем временный экземпляр для получения unique_id
            temp_sensor = sensor_class(*args)
            unique_id = temp_sensor.unique_id
            
            # Проверяем, не создавали ли мы уже этот unique_id в рамках текущего вызова
            if unique_id in seen_unique_ids:
                _LOGGER.debug(f"Entity {unique_id} already processed in this call, skipping")
                continue
            seen_unique_ids.add(unique_id)
            
            # Проверяем, не существует ли уже такая сущность в реестре
            existing_entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id
            )
            
            # Если сущность уже существует в реестре, пропускаем создание
            if existing_entity_id:
                existing_entry = entity_registry.async_get(existing_entity_id)
                if existing_entry:
                    _LOGGER.debug(
                        f"Entity {unique_id} already exists in registry "
                        f"(entity_id={existing_entity_id}), skipping"
                    )
                continue
            
            # Дополнительная проверка: существует ли сущность в hass.states
            # Это защищает от повторного создания при повторном вызове async_setup_entry
            if unique_id in hass.data.setdefault(DOMAIN, {}).setdefault("added_entities", set()):
                _LOGGER.debug(f"Entity {unique_id} was already added in previous setup call, skipping")
                continue
            
            # Создаем полноценный экземпляр
            sensor = sensor_class(*args)
            entities.append(sensor)
            # Отмечаем сущность как добавленную
            hass.data[DOMAIN]["added_entities"].add(unique_id)
            _LOGGER.debug(f"Added sensor {unique_id} to entities list")
        
        _LOGGER.debug(f"Finished processing sensors for meter {serial} ({device_type})")
    
    if entities:
        _LOGGER.info(f"Adding {len(entities)} new entities for entry {config_entry.entry_id}")
        async_add_entities(entities)
    else:
        _LOGGER.debug(f"No new entities to add for entry {config_entry.entry_id}")


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
        # Уникальный ID сенсора включает тип устройства для избежания коллизий
        self._attr_unique_id = f"{serial}_{device_type}_{sensor_type}"
        self._attr_should_poll = False
        
        # Set device info - используем комбинацию serial и device_type для уникальной идентификации устройства
        # Это предотвращает дублирование устройств когда один серийный номер используется для разных типов (газ/вода)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial}_{device_type}")},
            name=device_name,
            manufacturer="Elehant",
            model="Gas Meter" if device_type == DEVICE_TYPE_GAS else "Water Meter",
            sw_version="1.0",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data:
            value = self._get_state_from_data(self.coordinator.data)
            # Для числовых сенсоров (water, gas) с state_class total_increasing
            # нельзя устанавливать строковые значения. Если значение None,
            # оставляем предыдущее значение или устанавливаем None (HA обработает как unavailable)
            if value is not None:
                self._attr_native_value = value
            # Если value is None, не меняем _attr_native_value, чтобы HA использовал
            # последнее известное значение или показал unavailable
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
            # При восстановлении состояния проверяем, что значение числовое
            # Если состояние 'unknown' или не может быть преобразовано в float,
            # устанавливаем None вместо строкового значения
            try:
                restored_value = float(last_state.state)
                self._attr_native_value = restored_value
            except (ValueError, TypeError):
                # Если не удалось преобразовать (например, 'unknown'), оставляем None
                self._attr_native_value = None

    def _get_state_from_data(self, data: dict) -> float | None:
        if "value" not in data:
            return None
        raw_value = data["value"]
        
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
        return data.get("temperature")


class ElehantBatterySensor(ElehantBaseSensor):
    """Sensor for battery level."""

    def __init__(self, coordinator, serial, device_type, device_name, location=""):
        super().__init__(coordinator, serial, device_type, device_name, SENSOR_TYPE_BATTERY, location)
        self._attr_name = f"{device_name} Battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = STATE_CLASS_MEASUREMENT

    def _get_state_from_data(self, data: dict) -> int:
        return 100  # Placeholder

"""Bluetooth scanner with history for Elehant meters using HA Bluetooth API."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DEVICE_TYPE_GAS,
    DEVICE_TYPE_WATER,
    DOMAIN,
    ELEHANT_MARKER,
    IDX_MARKER,
    IDX_SERIAL_END,
    IDX_SERIAL_START,
    IDX_TEMP_END,
    IDX_TEMP_START,
    IDX_VALUE_END,
    IDX_VALUE_START,
    MAC_MODEL_IDX,
    MAC_PREFIXES,
    MAC_TYPE_IDX,
    SEPARATOR,
    SIGNAL_NEW_DATA,
)

_LOGGER = logging.getLogger(__name__)

def extract_info_from_mac(mac: str) -> dict | None:
    """Extract model, type and serial from MAC address."""
    if not mac or not any(mac.startswith(p) for p in MAC_PREFIXES):
        return None
    
    parts = mac.split(":")
    if len(parts) != 6:
        return None
    
    try:
        # Префикс может быть B0 или B1 - берем байт модели из 2-го октета
        model_hex = parts[MAC_MODEL_IDX]  # Это всегда второй октет
        type_hex = parts[MAC_TYPE_IDX]    # Это третий октет
        serial_hex = parts[3] + parts[4] + parts[5]
        
        model = int(model_hex, 16)
        type_byte = int(type_hex, 16)
        serial = int(serial_hex, 16)
        
        # Возвращаем информацию из MAC, но не определяем тип устройства по модели
        # Тип устройства будет определен при настройке интеграции
        return {
            "serial": serial,
            "model": model,
            "type_byte": type_byte,
            "device_type": None,  # Не определяем тип по MAC
            "mac": mac,
        }
    except (ValueError, IndexError) as e:
        _LOGGER.debug(f"Error parsing MAC {mac}: {e}")
        return None

def parse_meter_data(manufacturer_data: dict[int, bytes]) -> dict[str, Any] | None:
    """Parse manufacturer data from Elehant meter."""
    if not manufacturer_data:
        return None
    
    # Get the data (usually on manufacturer ID 0xFFFF)
    data = None
    for mfr_id, mfr_data in manufacturer_data.items():
        if mfr_id == 0xFFFF or mfr_id == 65535:
            data = mfr_data
            break
    
    if not data:
        return None
    
    # Проверяем минимальную длину данных
    if len(data) < 16:  # Минимальная длина для разбора основных полей
        _LOGGER.debug(f"Неверная длина данных: {len(data)} байт")
        return None
    
    # Проверяем маркер Elehant (0x80) - может быть на разных позициях
    marker_pos = -1
    for i in range(min(4, len(data))):  # Ищем маркер в первых 4 байтах
        if data[i] == ELEHANT_MARKER:
            marker_pos = i
            break
    
    if marker_pos == -1:
        _LOGGER.debug(f"Маркер Elehant (0x{ELEHANT_MARKER:02X}) не найден")
        return None
    
    offset = marker_pos
    _LOGGER.debug(f"Найден маркер Elehant на позиции {offset}, длина данных: {len(data)}")
    
    # Проверяем, достаточно ли данных для разбора
    if len(data) - offset < 16:  # Нужно минимум 16 байт после маркера
        _LOGGER.debug(f"Недостаточно данных для разбора: {len(data)-offset} байт после маркера")
        return None
    
    try:
        # Серийный номер (3 байта, little-endian) - на позиции offset+6
        serial_bytes = data[offset + 6:offset + 9]
        serial = int.from_bytes(serial_bytes, byteorder="little")
        
        # Значение счетчика (4 байта, little-endian) - на позиции offset+9
        value_bytes = data[offset + 9:offset + 13]
        value = int.from_bytes(value_bytes, byteorder="little")
        
        # Температура (2 байта, little-endian) - на позиции offset+14
        temp_bytes = data[offset + 14:offset + 16]
        temp_raw = int.from_bytes(temp_bytes, byteorder="little")
        temperature = temp_raw / 100.0
        
        # Последовательность (1 байт) - на позиции offset+1
        sequence = data[offset + 1]
        
        return {
            "serial": serial,
            "value": value,
            "temperature": temperature,
            "sequence": sequence,
            "raw_data": data.hex(),
        }
    except Exception as e:
        _LOGGER.debug(f"Ошибка парсинга: {e}, данные: {data.hex()}")
        return None


class ElehantHistoryScanner:
    """Scanner using HA Bluetooth API that keeps history of seen Elehant devices."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the history scanner."""
        self.hass = hass
        self._cancel_callback: Callable | None = None
        self._scan_task: asyncio.Task | None = None
        
        # История устройств: { mac: { ... } }
        self.seen_devices: dict[str, dict] = {}
        
        _LOGGER.info("Elehant History Scanner initialized with HA Bluetooth API")

    def _detection_callback(
        self, 
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange
    ) -> None:
        """Handle device detection from HA Bluetooth API."""
    
        # 🚫 БЛОКИРУЕМ ИЗВЕСТНОГО СПАМЕРА
        blocked_macs = {
        "1A:EC:A8:F2:57:22",  # Этот сука
        # "можно добавить еще mac через запятую"
        }

        if service_info.address in blocked_macs:
            return  # Игнорируем нахуй
        
        # Выводим всё для отладки
        _LOGGER.debug(f"HA BLE: {service_info.address} RSSI:{service_info.rssi}")
        
        # Проверяем MAC
        if not any(service_info.address.startswith(p) for p in MAC_PREFIXES):
            return
        
        # Кричим, если нашли B0:
        _LOGGER.debug(f"!!! НАШЕЛ ПОТЕНЦИАЛЬНЫЙ ЭЛЕХАНТ: {service_info.address}")
        _LOGGER.debug(f"!!! Данные производителя: {service_info.manufacturer_data}")
        _LOGGER.debug(f"!!! RSSI: {service_info.rssi}")
        
        # Извлекаем информацию из MAC
        mac_info = extract_info_from_mac(service_info.address)
        if not mac_info:
            _LOGGER.warning(f"Не удалось извлечь данные из MAC {service_info.address}")
            return
        
        # Парсим данные пакета
        parsed = parse_meter_data(service_info.manufacturer_data)
        if parsed:
           _LOGGER.info(f"Получены данные от счетчика {parsed['serial']}: значение={parsed['value']}, температура={parsed['temperature']}°C")
        else:
            _LOGGER.warning(f"Не удалось распарсить данные от {service_info.address}, raw data: {service_info.manufacturer_data}")
        
        now = time.time()
        mac = service_info.address
        
        # Обновляем историю
        self._update_history(mac, mac_info, parsed, service_info, now)

    def _update_history(self, mac: str, mac_info: dict, parsed: dict | None, service_info: bluetooth.BluetoothServiceInfoBleak, timestamp: float):
        """Update the device history."""
        if mac not in self.seen_devices:
            # Новое устройство
            self.seen_devices[mac] = {
                "serial": mac_info["serial"],
                "model": mac_info["model"],
                "type_byte": mac_info["type_byte"],
                "device_type": mac_info["device_type"],
                "mac": mac,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "packets": 0,
                "best_rssi": service_info.rssi,
                "manufacturer_data": {},
            }
            _LOGGER.info(f"New Elehant device discovered: {mac} (SN:{mac_info['serial']}, Model:{mac_info['model']}, Type:{mac_info['device_type']})")
        
        # Обновляем существующее
        device_info = self.seen_devices[mac]
        device_info["last_seen"] = timestamp
        device_info["packets"] += 1
        if service_info.rssi > device_info["best_rssi"]:
            device_info["best_rssi"] = service_info.rssi
        
        # Если есть данные счетчика, сохраняем последние показания
        if parsed:
            device_info["last_value"] = parsed["value"]
            device_info["last_temperature"] = parsed["temperature"]
            device_info["last_raw"] = parsed["raw_data"]
            
            # Используем тип устройства из конфигурации, а не из MAC
            # Если устройство уже настроено, используем его тип
            configured_device_type = None
            for key, meter_config in self.hass.data.get(DOMAIN, {}).items():
                if key.startswith("meter_") and meter_config.get(CONF_DEVICE_SERIAL) == mac_info["serial"]:
                    configured_device_type = meter_config.get(CONF_DEVICE_TYPE)
                    break
            
            # Если тип устройства известен из настройки, используем его
            if configured_device_type:
                device_info["device_type"] = configured_device_type
            elif device_info["device_type"] is None:
                # Если тип все еще не определен, используем информацию из MAC как резерв
                device_info["device_type"] = mac_info["device_type"]
            
            # Если этот счетчик уже настроен, шлем обновление
            self._notify_meter_update(mac_info["serial"], parsed, service_info.rssi)

    def _notify_meter_update(self, serial: int, parsed: dict, rssi: int):
        """Notify a configured meter about new data."""
        coordinator_key = f"coordinator_{serial}"
        if coordinator_key in self.hass.data.get(DOMAIN, {}):
            coordinator = self.hass.data[DOMAIN][coordinator_key]
            update_data = {
                "serial": serial,
                "value": parsed["value"],
                "temperature": parsed["temperature"],
                "rssi": rssi,
            }
            coordinator.update_data(update_data)
            async_dispatcher_send(self.hass, SIGNAL_NEW_DATA, update_data)

    def get_recent_devices(self, hours: int = 24) -> list[dict]:
        """Get devices seen in the last N hours."""
        now = time.time()
        cutoff = now - (hours * 3600)
        recent = []
        for mac, info in self.seen_devices.items():
            if info["last_seen"] >= cutoff:
                recent.append({"mac": mac, **info})
        return recent

    async def start(self):
        """Start listening for Bluetooth devices via HA API."""
        _LOGGER.info("Starting Elehant history scanner via HA Bluetooth API")
        
        from homeassistant.components.bluetooth import (
            async_register_callback,
            BluetoothScanningMode,
        )
        
        self._cancel_callback = async_register_callback(
            self.hass,
            self._detection_callback,
            {},
            BluetoothScanningMode.ACTIVE,  # ← АКТИВНЫЙ РЕЖИМ!
        )
        
        _LOGGER.info("Elehant history scanner started successfully")

    async def stop(self):
        """Stop listening for Bluetooth devices."""
        _LOGGER.info("Stopping Elehant history scanner")
        if self._cancel_callback:
            self._cancel_callback()
            self._cancel_callback = None
        _LOGGER.info("Elehant history scanner stopped")

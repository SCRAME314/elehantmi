"""Bluetooth scanner with history for Elehant meters."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DEVICE_TYPE_GAS,
    DEVICE_TYPE_WATER,
    DOMAIN,
    ELEHANT_MARKER,
    GAS_MODELS,
    IDX_MARKER,
    IDX_SERIAL_END,
    IDX_SERIAL_START,
    IDX_TEMP_END,
    IDX_TEMP_START,
    IDX_VALUE_END,
    IDX_VALUE_START,
    MAC_MODEL_IDX,
    MAC_PREFIX,
    MAC_TYPE_IDX,
    SEPARATOR,
    SIGNAL_NEW_DATA,
    WATER_MODELS,
)

_LOGGER = logging.getLogger(__name__)

def extract_info_from_mac(mac: str) -> dict | None:
    """Extract model, type and serial from MAC address."""
    if not mac.startswith(MAC_PREFIX):
        return None
    
    parts = mac.split(":")
    if len(parts) != 6:
        return None
    
    try:
        model_hex = parts[MAC_MODEL_IDX]
        type_hex = parts[MAC_TYPE_IDX]
        serial_hex = parts[3] + parts[4] + parts[5]
        
        model = int(model_hex, 16)
        type_byte = int(type_hex, 16)
        serial = int(serial_hex, 16)
        
        # Определяем тип устройства по модели
        device_type = None
        if model in GAS_MODELS:
            device_type = DEVICE_TYPE_GAS
        elif model in WATER_MODELS:
            device_type = DEVICE_TYPE_WATER
        else:
            return None  # Неизвестная модель
        
        return {
            "serial": serial,
            "model": model,
            "type_byte": type_byte,
            "device_type": device_type,
            "mac": mac,
        }
    except (ValueError, IndexError):
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
    
    if not data or len(data) < 21:
        return None
    
    if len(data) > IDX_MARKER and data[IDX_MARKER] != ELEHANT_MARKER:
        return None
    
    if len(data) > IDX_SEPARATOR and data[IDX_SEPARATOR] != SEPARATOR:
        return None
    
    try:
        serial_bytes = data[IDX_SERIAL_START:IDX_SERIAL_END]
        serial = int.from_bytes(serial_bytes, byteorder="little")
        
        value_bytes = data[IDX_VALUE_START:IDX_VALUE_END]
        value = int.from_bytes(value_bytes, byteorder="little")
        
        temp_bytes = data[IDX_TEMP_START:IDX_TEMP_END]
        temp_raw = int.from_bytes(temp_bytes, byteorder="little")
        temperature = temp_raw / 100.0
        
        sequence = data[5] if len(data) > 5 else 0
        
        return {
            "serial": serial,
            "value": value,
            "temperature": temperature,
            "sequence": sequence,
            "raw_data": data.hex(),
        }
    except Exception as e:
        _LOGGER.debug(f"Error parsing meter data: {e}")
        return None


class ElehantHistoryScanner:
    """Continuous BLE scanner that keeps history of seen Elehant devices."""

    def __init__(self, hass: HomeAssistant, adapter: str = "hci0") -> None:
        """Initialize the history scanner."""
        self.hass = hass
        self.adapter = adapter
        self.scanner: BleakScanner | None = None
        self._scan_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        
        # История устройств: { mac: { ... } }
        self.seen_devices: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        
        _LOGGER.info("Elehant History Scanner initialized")

    async def start(self) -> None:
        """Start continuous scanning."""
        if self.scanner is not None:
            _LOGGER.warning("Scanner already running")
            return
        
        _LOGGER.info(f"Starting Elehant history scanner on adapter {self.adapter}")
        
        try:
            self.scanner = BleakScanner(
                detection_callback=self._detection_callback,
                adapter=self.adapter,
            )
            self._stop_event.clear()
            self._scan_task = asyncio.create_task(self._run_scanner())
            _LOGGER.info("Elehant history scanner started successfully")
        except Exception as e:
            _LOGGER.error(f"Failed to start scanner: {e}")
            self.scanner = None
            raise

    async def _run_scanner(self) -> None:
        """Run scanner continuously."""
        try:
            await self.scanner.start()
            await self._stop_event.wait()
        except asyncio.CancelledError:
            _LOGGER.debug("Scanner task cancelled")
        except Exception as e:
            _LOGGER.error(f"Scanner error: {e}")
        finally:
            if self.scanner:
                await self.scanner.stop()
                self.scanner = None

    def _detection_callback(
        self, device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Handle device detection and update history."""
        # Отладка: видим все BLE устройства
        _LOGGER.debug(f"BLE: {device.address} RSSI:{advertisement_data.rssi}")
        
        # Проверяем MAC
        mac_info = extract_info_from_mac(device.address)
        if not mac_info:
            return  # Не наше устройство или неизвестная модель
        
        # Парсим данные пакета
        parsed = parse_meter_data(advertisement_data.manufacturer_data)
        
        now = time.time()
        mac = device.address
        
        # Обновляем историю (в потокобезопасном режиме)
        self.hass.loop.call_soon_threadsafe(
            self._update_history, mac, mac_info, parsed, advertisement_data, now
        )

    def _update_history(self, mac: str, mac_info: dict, parsed: dict | None, adv_data: AdvertisementData, timestamp: float):
        """Update the device history (runs in HA event loop)."""
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
                "best_rssi": adv_data.rssi,
                "manufacturer_data": {},
            }
            _LOGGER.info(f"New Elehant device discovered: {mac} (SN:{mac_info['serial']}, Model:{mac_info['model']}, Type:{mac_info['device_type']})")
        
        # Обновляем существующее
        device_info = self.seen_devices[mac]
        device_info["last_seen"] = timestamp
        device_info["packets"] += 1
        if adv_data.rssi > device_info["best_rssi"]:
            device_info["best_rssi"] = adv_data.rssi
        
        # Если есть данные счетчика, сохраняем последние показания
        if parsed:
            device_info["last_value"] = parsed["value"]
            device_info["last_temperature"] = parsed["temperature"]
            device_info["last_raw"] = parsed["raw_data"]
            
            # Если этот счетчик уже настроен, шлем обновление
            self._notify_meter_update(mac_info["serial"], parsed, adv_data.rssi)

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

    async def stop(self):
        """Stop scanning."""
        _LOGGER.info("Stopping Elehant history scanner")
        self._stop_event.set()
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        if self.scanner:
            await self.scanner.stop()
            self.scanner = None
        _LOGGER.info("Elehant history scanner stopped")

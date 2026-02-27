"""Bluetooth scanner for Elehant meters."""
from __future__ import annotations

import asyncio
import logging
import struct
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
    IDX_MARKER,
    IDX_SERIAL_END,
    IDX_SERIAL_START,
    IDX_TEMP_END,
    IDX_TEMP_START,
    IDX_VALUE_END,
    IDX_VALUE_START,
    MAC_PREFIX,
    MAC_TYPE_IDX,
    PACKET_HEADER,
    SEPARATOR,
    SIGNAL_NEW_DATA,
)

_LOGGER = logging.getLogger(__name__)


def extract_serial_from_mac(mac: str) -> int | None:
    """Extract serial number from MAC address."""
    if not mac.startswith(MAC_PREFIX):
        return None
    
    parts = mac.split(":")
    if len(parts) != 6:
        return None
    
    try:
        # Serial is in last 3 octets (SS:SS:SS)
        serial_hex = parts[3] + parts[4] + parts[5]
        return int(serial_hex, 16)
    except (ValueError, IndexError):
        return None


def get_device_type_from_mac(mac: str) -> str | None:
    """Get device type from MAC address."""
    if not mac.startswith(MAC_PREFIX):
        return None
    
    parts = mac.split(":")
    if len(parts) != 6:
        return None
    
    try:
        type_byte = int(parts[MAC_TYPE_IDX], 16)
        if type_byte == 0x01:
            return DEVICE_TYPE_GAS
        elif type_byte in [0x02, 0x03, 0x04]:
            return DEVICE_TYPE_WATER
        else:
            return None
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
    
    if not data or len(data) < 21:  # Need at least 21 bytes
        return None
    
    # Check for Elehant marker at byte 4
    if len(data) > IDX_MARKER and data[IDX_MARKER] != ELEHANT_MARKER:
        _LOGGER.debug(f"Not an Elehant device: marker=0x{data[IDX_MARKER]:02X}")
        return None
    
    # Check separator at byte 17
    if len(data) > IDX_SEPARATOR and data[IDX_SEPARATOR] != SEPARATOR:
        _LOGGER.debug(f"Invalid separator: 0x{data[IDX_SEPARATOR]:02X}")
        return None
    
    try:
        # Extract serial number (3 bytes, little-endian)
        serial_bytes = data[IDX_SERIAL_START:IDX_SERIAL_END]
        serial = int.from_bytes(serial_bytes, byteorder="little")
        
        # Extract meter value (4 bytes, little-endian)
        value_bytes = data[IDX_VALUE_START:IDX_VALUE_END]
        value = int.from_bytes(value_bytes, byteorder="little")
        
        # Extract temperature (2 bytes, little-endian)
        temp_bytes = data[IDX_TEMP_START:IDX_TEMP_END]
        temp_raw = int.from_bytes(temp_bytes, byteorder="little")
        temperature = temp_raw / 100.0
        
        # Get sequence number (optional)
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


class ElehantScanner:
    """Continuous BLE scanner for Elehant meters."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize scanner."""
        self.hass = hass
        self.scanner: BleakScanner | None = None
        self._callbacks: list[Callable] = []
        self._scan_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self.discovered_devices: dict[str, dict] = {}
        self.configured_meters: dict[int, dict] = {}
        self._lock = asyncio.Lock()

    async def start(self, adapter: str = "hci0") -> None:
        """Start continuous scanning."""
        if self.scanner is not None:
            _LOGGER.warning("Scanner already running")
            return
        
        _LOGGER.info(f"Starting Elehant scanner on adapter {adapter}")
        
        try:
            # Load configured meters
            await self._load_configured_meters()
            
            # Create scanner with detection callback
            self.scanner = BleakScanner(
                detection_callback=self._detection_callback,
                adapter=adapter,
            )
            
            # Start scanning in background
            self._stop_event.clear()
            self._scan_task = asyncio.create_task(self._run_scanner())
            
        except Exception as e:
            _LOGGER.error(f"Failed to start scanner: {e}")
            self.scanner = None
            raise

    async def _load_configured_meters(self) -> None:
        """Load configured meters from hass data."""
        configured = {}
        
        # Get all meter configurations
        for key, value in self.hass.data.get(DOMAIN, {}).items():
            if key.startswith("meter_"):
                meter_config = value
                serial = meter_config.get("serial")
                if serial:
                    configured[serial] = meter_config
        
        self.configured_meters = configured
        _LOGGER.debug(f"Loaded {len(configured)} configured meters")

    async def _run_scanner(self) -> None:
        """Run scanner continuously."""
        try:
            await self.scanner.start()
            _LOGGER.info("Scanner started successfully")
            
            # Keep running until stopped
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
        """Handle device detection."""
        # Log all BLE devices for debugging
        _LOGGER.debug(f"BLE Device: {device.address} - RSSI: {device.rssi} - Data: {advertisement_data.manufacturer_data}")
        
        # Check if it's an Elehant device by MAC prefix
        if not device.address.startswith(MAC_PREFIX):
            return
        
        _LOGGER.info(f"Found potential Elehant device: {device.address}")
        
        # Extract serial from MAC
        mac_serial = extract_serial_from_mac(device.address)
        if not mac_serial:
            return
        
        # Determine device type from MAC
        device_type = get_device_type_from_mac(device.address)
        
        # Parse manufacturer data
        parsed_data = parse_meter_data(advertisement_data.manufacturer_data)
        
        if parsed_data:
            serial = parsed_data["serial"]
            _LOGGER.info(
                f"Valid Elehant packet from {device.address}: "
                f"ID={serial}, Value={parsed_data['value']}, "
                f"Temp={parsed_data['temperature']:.1f}°C"
            )
            
            # Add to discovered devices
            self.discovered_devices[device.address] = {
                "serial": serial,
                "device_type": device_type,
                "mac": device.address,
                "last_seen": parsed_data,
            }
            
            # Check if this meter is configured
            if serial in self.configured_meters:
                _LOGGER.debug(f"Updating data for configured meter {serial}")
                
                # Create update data
                update_data = {
                    "serial": serial,
                    "value": parsed_data["value"],
                    "temperature": parsed_data["temperature"],
                    "rssi": device.rssi,
                    "mac": device.address,
                }
                
                # Update coordinator
                self.hass.loop.call_soon_threadsafe(
                    self._update_meter_data, serial, update_data
                )
        else:
            _LOGGER.debug(f"Ignoring invalid Elehant packet from {device.address}")

    def _update_meter_data(self, serial: int, data: dict) -> None:
        """Update meter data in Home Assistant."""
        coordinator_key = f"coordinator_{serial}"
        if coordinator_key in self.hass.data[DOMAIN]:
            coordinator = self.hass.data[DOMAIN][coordinator_key]
            coordinator.update_data(data)
            
            # Dispatch signal for sensors
            async_dispatcher_send(self.hass, SIGNAL_NEW_DATA, data)

    def async_add_listener(self, callback: Callable) -> Callable:
        """Add a listener for scanner events."""
        self._callbacks.append(callback)
        
        def remove_listener():
            self._callbacks.remove(callback)
        
        return remove_listener

    async def stop(self) -> None:
        """Stop scanning."""
        _LOGGER.info("Stopping Elehant scanner")
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
        
        _LOGGER.info("Scanner stopped")

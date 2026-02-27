"""Constants for Elehant Meter Integration."""
from __future__ import annotations

from homeassistant.const import (
    UnitOfTemperature,
    UnitOfVolume,
    PERCENTAGE,
)

DOMAIN = "elehantmi"
STORAGE_KEY = f"{DOMAIN}_storage"
STORAGE_VERSION = 1

# Configuration keys
CONF_DEVICE_SERIAL = "serial"
CONF_DEVICE_TYPE = "device_type"
CONF_DEVICE_NAME = "name"
CONF_LOCATION = "location"
CONF_UNITS = "units"
CONF_MANUAL_METERS = "manual_meters"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_BT_ADAPTER = "bt_adapter"
CONF_SELECTED_BT_ADAPTER = "selected_bt_adapter"

# Default values
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_DEVICE_NAME = "Elehant Meter"
DEFAULT_SCAN_TIMEOUT = 60

# Device types
DEVICE_TYPE_GAS = "gas"
DEVICE_TYPE_WATER = "water"

# Unit types
UNIT_CUBIC_METERS = "m³"
UNIT_LITERS = "L"

# Sensor types
SENSOR_TYPE_METER = "meter"
SENSOR_TYPE_TEMPERATURE = "temperature"
SENSOR_TYPE_BATTERY = "battery"

# Signal for data updates
SIGNAL_NEW_DATA = f"{DOMAIN}_new_data"

# Packet parsing constants
PACKET_HEADER = b"\x14\xff\xff\xff"
ELEHANT_MARKER = 0x80
SEPARATOR = 0x7F

# Byte indices in manufacturer data
IDX_MARKER = 4
IDX_SEQUENCE = 5
IDX_SERIAL_START = 10
IDX_SERIAL_END = 13
IDX_VALUE_START = 13
IDX_VALUE_END = 17
IDX_SEPARATOR = 17
IDX_TEMP_START = 18
IDX_TEMP_END = 20

# MAC address indices
MAC_PREFIXES = {"B0:", "B1:"}
MAC_MODEL_IDX = 1
MAC_TYPE_IDX = 2

# Gas models (твои данные)
GAS_MODELS = {
    1, 2, 3, 4, 5, 16, 17, 18, 19, 20, 32, 33, 34, 35, 36, 48, 49, 50, 51, 52,
    64, 65, 66, 67, 68, 80, 81, 82, 83, 84
}

# Water models (твои данные)
WATER_MODELS = {
    1, 2, 3, 4, 5, 6
}

# Sensor units
UNIT_TEMPERATURE = UnitOfTemperature.CELSIUS
UNIT_BATTERY = PERCENTAGE

# Sensor device classes
DEVICE_CLASS_METER = "water"
DEVICE_CLASS_TEMPERATURE = "temperature"
DEVICE_CLASS_BATTERY = "battery"

# Sensor state classes
STATE_CLASS_MEASUREMENT = "measurement"
STATE_CLASS_TOTAL_INCREASING = "total_increasing"

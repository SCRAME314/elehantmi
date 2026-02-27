"""Data coordinator for Elehant Meter Integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ElehantDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Elehant meter data."""

    def __init__(self, hass: HomeAssistant, serial: int) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),  # Will be updated by scanner
        )
        self.serial = serial
        self._data = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from scanner (passive)."""
        # Data is pushed from scanner via async_set_updated_data
        return self._data

    def update_data(self, data: dict[str, Any]) -> None:
        """Update data from scanner."""
        self._data = data
        self.async_set_updated_data(data)

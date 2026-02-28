"""Auto-discovery logic for Elehant Meter Integration."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN
from .scanner import ElehantHistoryScanner

_LOGGER = logging.getLogger(__name__)


class ElehantAutoDiscover:
    """Handles auto-discovery of Elehant meters."""

    def __init__(
        self,
        hass: HomeAssistant,
        flow: ConfigFlow,
        scanner: ElehantHistoryScanner,
        timeout: int = 300,  # 5 минут по умолчанию
    ) -> None:
        """Initialize auto-discover."""
        self.hass = hass
        self.flow = flow
        self.scanner = scanner
        self.timeout = timeout
        
        self.discovered_devices: list[dict] = []
        self.scan_task: asyncio.Task | None = None
        self.start_time: float | None = None
        self.duration: int = 0
        self._update_callback: Callable[[], Coroutine] | None = None
        self._stop_callback: Callable[[], Coroutine] | None = None

    async def start_scan(self) -> None:
        """Start background scanning."""
        self.start_time = time.time()
        self.duration = 0
        
        async def _scan_loop():
            """Main scanning loop."""
            try:
                # Бесконечное сканирование пока не остановят
                while True:
                    await asyncio.sleep(5)  # Проверяем каждые 5 секунд
                    
                    # Получаем устройства из истории сканера за последние 24 часа
                    recent = self.scanner.get_recent_devices(hours=24)
                    
                    # Фильтруем уже настроенные
                    new_devices = []
                    for dev in recent:
                        unique_id = str(dev["serial"])
                        if unique_id not in self.flow._async_current_ids():
                            new_devices.append(dev)
                    
                    # Если есть изменения, обновляем список
                    if new_devices != self.discovered_devices:
                        self.discovered_devices = new_devices
                        _LOGGER.info(f"Найдено {len(new_devices)} новых устройств")
                        
                        # Вызываем колбэк обновления с await
                        if self._update_callback:
                            await self._update_callback()
                    
                    # Обновляем время
                    self.duration = int(time.time() - self.start_time)
                    
                    # Проверяем таймаут
                    if self.duration > self.timeout:
                        _LOGGER.info(f"Достигнут таймаут сканирования ({self.timeout} сек)")
                        if self._stop_callback:
                            await self._stop_callback()
                        break
                        
            except asyncio.CancelledError:
                _LOGGER.debug("Auto-discover cancelled by user")
                raise
            except Exception as err:
                _LOGGER.error("Auto-discover error: %s", err)
                raise
        
        self.scan_task = asyncio.create_task(_scan_loop())

    def stop_scan(self) -> None:
        """Stop background scanning."""
        if self.scan_task and not self.scan_task.done():
            self.scan_task.cancel()
            self.scan_task = None

    @property
    def is_scanning(self) -> bool:
        """Return True if scanning is active."""
        return self.scan_task is not None and not self.scan_task.done()

    @property
    def time_elapsed(self) -> str:
        """Get formatted elapsed time."""
        if self.start_time is None:
            return "00:00"
        minutes = self.duration // 60
        seconds = self.duration % 60
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def devices_count(self) -> int:
        """Get number of discovered devices."""
        return len(self.discovered_devices)

    def on_update(self, callback: Callable[[], Coroutine]) -> None:
        """Set callback for updates."""
        self._update_callback = callback

    def on_stop(self, callback: Callable[[], Coroutine]) -> None:
        """Set callback for stop (timeout)."""
        self._stop_callback = callback

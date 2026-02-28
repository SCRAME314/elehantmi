"""Auto-discovery logic for Elehant Meter Integration."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

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
        timeout: int = 300,
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
        self._update_callback: Callable[[], None] | None = None
        self._stop_callback: Callable[[], None] | None = None
        
        # Логи для отображения в интерфейсе
        self.log_messages: list[str] = []
        self.max_logs = 8  # Показываем последние 8 сообщений

    def add_log(self, message: str) -> None:
        """Add message to log buffer and trigger update."""
        timestamp = time.strftime("%H:%M:%S")
        self.log_messages.append(f"{timestamp} - {message}")
        
        # Оставляем только последние max_logs сообщений
        if len(self.log_messages) > self.max_logs:
            self.log_messages = self.log_messages[-self.max_logs:]
        
        # Вызываем обновление интерфейса
        if self._update_callback:
            self.hass.loop.call_soon_threadsafe(self._update_callback)

    async def start_scan(self) -> None:
        """Start background scanning."""
        self.start_time = time.time()
        self.duration = 0
        self.log_messages = []
        self.add_log("🚀 Сканирование запущено")
        
        async def _scan_loop():
            """Main scanning loop."""
            try:
                while True:
                    await asyncio.sleep(5)
                    
                    # Получаем устройства из истории сканера
                    recent = self.scanner.get_recent_devices(hours=24)
                    
                    # Фильтруем уже настроенные
                    new_devices = []
                    for dev in recent:
                        unique_id = str(dev["serial"])
                        if unique_id not in self.flow._async_current_ids():
                            new_devices.append(dev)
                    
                    # Если есть новые устройства
                    if new_devices:
                        # Проверяем, действительно ли это новые (не были в списке)
                        truly_new = [
                            dev for dev in new_devices 
                            if dev not in self.discovered_devices
                        ]
                        
                        if truly_new:
                            self.discovered_devices = new_devices
                            self.add_log(f"📡 Найдено {len(truly_new)} новых устройств")
                            for dev in truly_new:
                                device_type = "🔥 Газ" if dev['device_type'] == 'gas' else "💧 Вода"
                                self.add_log(
                                    f"  • {device_type} {dev['serial']} "
                                    f"(модель {dev['model']}, RSSI:{dev['best_rssi']})"
                                )
                    
                    # Обновляем время
                    self.duration = int(time.time() - self.start_time)
                    
                    # Проверяем таймаут
                    if self.duration > self.timeout:
                        self.add_log(f"⏰ Достигнут таймаут ({self.timeout} сек)")
                        if self._stop_callback:
                            self.hass.loop.call_soon_threadsafe(self._stop_callback)
                        break
                        
            except asyncio.CancelledError:
                self.add_log("⏹️ Сканирование остановлено пользователем")
                _LOGGER.debug("Auto-discover cancelled by user")
                raise
            except Exception as err:
                self.add_log(f"❌ Ошибка: {err}")
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

    @property
    def logs_text(self) -> str:
        """Get formatted logs text."""
        if not self.log_messages:
            return "  Ожидание устройств..."
        return "\n".join(self.log_messages)

    def on_update(self, callback: Callable[[], None]) -> None:
        """Set callback for updates."""
        self._update_callback = callback

    def on_stop(self, callback: Callable[[], None]) -> None:
        """Set callback for stop (timeout)."""
        self._stop_callback = callback

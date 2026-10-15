"""
Wildberries Browser Client Manager (Playwright, async)
"""

import asyncio
import random
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

from src.core.logger import get_logger
from src.core.config import Config

logger = get_logger(__name__)

WB_PROFILE_DIR = Config.DATA_DIR / 'wb' / 'browser_profile'


class WBClientManager:
    """Менеджер браузера для работы с Wildberries (асинхронный, общий контекст на несколько вкладок)."""

    def __init__(
        self,
        profile: str = '',
        proxy_address: Optional[str] = None,
        proxy_port: Optional[int] = None,
        proxy_username: Optional[str] = None,
        proxy_password: Optional[str] = None,
        proxy_type: str = "http"
    ):
        self.profile = profile
        self.proxy_address = proxy_address
        self.proxy_port = proxy_port
        self.proxy_username = proxy_username
        self.proxy_password = proxy_password
        self.proxy_type = (proxy_type or "http").lower()

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Optional[Page]:
        return self._page

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

    async def connect(self) -> bool:
        """Запустить браузер через Playwright (асинхронно)."""
        WB_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        proxy = None
        if self.proxy_address:
            scheme = "socks5" if self.proxy_type in ("socks5", "socks") else "http"
            server = f"{scheme}://{self.proxy_address}:{self.proxy_port}"
            proxy = {"server": server}
            if self.proxy_username:
                proxy["username"] = self.proxy_username
                proxy["password"] = self.proxy_password or ""
            logger.info(f"Прокси: {self.proxy_address}:{self.proxy_port}")

        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-notifications",
            "--lang=ru-RU",
            "--start-maximized",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]

        logger.info("Запуск браузера...")
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(WB_PROFILE_DIR),
            headless=True,
            args=args,
            proxy=proxy,
            # Параметры устройства — имитация обычного десктопного Chrome
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            color_scheme="light",
        )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        logger.info("Браузер успешно запущен")
        await asyncio.sleep(random.uniform(2, 4))
        return True

    async def new_page(self) -> Page:
        """Открыть новую вкладку в общем контексте (для параллельной задачи)."""
        if not self._context:
            raise RuntimeError("Браузер не запущен — сначала вызовите connect()")
        return await self._context.new_page()

    async def disconnect(self):
        """Закрыть браузер."""
        try:
            if self._context:
                await self._context.close()
        except Exception as e:
            logger.warning(f"Ошибка закрытия контекста: {e}")
        finally:
            self._context = None
            self._page = None
            try:
                if self._playwright:
                    await self._playwright.stop()
            except Exception as e:
                logger.warning(f"Ошибка остановки Playwright: {e}")
            finally:
                self._playwright = None
        logger.info("Браузер закрыт")

"""
WhatsApp Web Browser Client Manager (Playwright, async, headed)

Каждый аккаунт WhatsApp = свой постоянный профиль браузера + обязательный прокси.
Окно открывается видимым (headless=False) — пользователь сам ведёт переписку
в настоящем WhatsApp Web.
"""

import asyncio
import base64
import random
from typing import Optional, Callable

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

from src.core.logger import get_logger
from src.core.config import Config

logger = get_logger(__name__)

WA_PROFILES_DIR = Config.DATA_DIR / 'whatsapp' / 'profiles'
WA_WEB_URL = "https://web.whatsapp.com"


class _ProxyAuthRelay:
    """Локальный HTTP-прокси на 127.0.0.1, который сам авторизуется на «настоящем» прокси.

    Зачем он нужен:
        Chromium НЕ подставляет логин/пароль прокси для сетевых запросов,
        которые делает Service Worker. WhatsApp Web регистрирует Service Worker
        на первом запуске и при ПОВТОРНОМ открытии профиля именно он ведёт
        навигацию — поэтому запрос упирается в ответ прокси «407 нужна
        авторизация», и Chrome показывает нативный диалог авторизации прокси.

        Релей решает это раз и навсегда: Chrome ходит на 127.0.0.1 БЕЗ
        авторизации, а релей добавляет заголовок ``Proxy-Authorization`` и
        форвардит трафик на реальный прокси. Chrome вообще никогда не видит 407,
        поэтому диалог не появляется ни для одного запроса (включая Service Worker).

    Поддерживает upstream-прокси типа http/https (метод CONNECT для HTTPS —
    это 99% трафика WhatsApp Web, плюс обычный HTTP).
    """

    def __init__(self, up_host: str, up_port: int, up_user: str, up_pass: str):
        self.up_host = up_host
        self.up_port = up_port
        self._auth = base64.b64encode(f"{up_user}:{up_pass or ''}".encode()).decode()
        self._server: Optional[asyncio.AbstractServer] = None
        self.local_port: Optional[int] = None

    async def start(self) -> int:
        """Поднять релей на свободном порту 127.0.0.1 и вернуть номер порта."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.local_port = self._server.sockets[0].getsockname()[1]
        return self.local_port

    async def stop(self):
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    @staticmethod
    async def _read_head(reader: asyncio.StreamReader) -> bytes:
        """Прочитать HTTP-заголовок целиком (до пустой строки)."""
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = await reader.read(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:  # защита от мусора
                break
        return data

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Перекачивать байты в одну сторону до закрытия соединения."""
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle(self, creader: asyncio.StreamReader, cwriter: asyncio.StreamWriter):
        ureader = uwriter = None
        try:
            head = await self._read_head(creader)
            if not head:
                cwriter.close()
                return

            first_line = head.split(b"\r\n", 1)[0].decode("latin1")
            parts = first_line.split(" ")
            method = parts[0].upper()

            ureader, uwriter = await asyncio.open_connection(self.up_host, self.up_port)

            if method == "CONNECT":
                # HTTPS-туннель: открываем CONNECT на реальном прокси с авторизацией,
                # отдаём его ответ браузеру, дальше просто перекачиваем байты.
                target = parts[1] if len(parts) > 1 else ""
                req = (
                    f"CONNECT {target} HTTP/1.1\r\n"
                    f"Host: {target}\r\n"
                    f"Proxy-Authorization: Basic {self._auth}\r\n\r\n"
                )
                uwriter.write(req.encode("latin1"))
                await uwriter.drain()
                up_resp = await self._read_head(ureader)
                cwriter.write(up_resp)
                await cwriter.drain()
            else:
                # Обычный HTTP: вставляем заголовок авторизации и форвардим запрос.
                line, rest = head.split(b"\r\n", 1)
                new_head = (
                    line + b"\r\nProxy-Authorization: Basic "
                    + self._auth.encode("latin1") + b"\r\n" + rest
                )
                uwriter.write(new_head)
                await uwriter.drain()

            await asyncio.gather(
                self._pipe(creader, uwriter),
                self._pipe(ureader, cwriter),
            )
        except Exception:
            pass
        finally:
            for w in (cwriter, uwriter):
                try:
                    if w:
                        w.close()
                except Exception:
                    pass


class WhatsAppClientManager:
    """Менеджер видимого окна WhatsApp Web для одного аккаунта (профиль + прокси)."""

    def __init__(
        self,
        profile_dir: str,
        proxy_address: str,
        proxy_port: int,
        proxy_username: Optional[str] = None,
        proxy_password: Optional[str] = None,
        proxy_type: str = "socks5",
    ):
        self.profile_dir = profile_dir
        self.proxy_address = proxy_address
        self.proxy_port = proxy_port
        self.proxy_username = proxy_username
        self.proxy_password = proxy_password
        self.proxy_type = (proxy_type or "socks5").lower()

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._on_close: Optional[Callable] = None
        self._relay: Optional[_ProxyAuthRelay] = None

    @property
    def page(self) -> Optional[Page]:
        return self._page

    @property
    def is_open(self) -> bool:
        return self._context is not None

    def _profile_path(self):
        return WA_PROFILES_DIR / self.profile_dir

    async def _resolve_proxy_for_chrome(self) -> dict:
        """Подготовить настройки прокси для Chrome.

        Для http-прокси С логином/паролем поднимаем локальный релей и отдаём
        Chrome адрес 127.0.0.1 без авторизации (см. ``_ProxyAuthRelay`` — это
        чинит диалог авторизации прокси при повторном открытии профиля).
        Для остальных случаев (без авторизации или socks5) передаём прокси
        напрямую, как раньше.
        """
        scheme = "socks5" if self.proxy_type in ("socks5", "socks") else "http"

        if scheme == "http" and self.proxy_username:
            self._relay = _ProxyAuthRelay(
                self.proxy_address, self.proxy_port,
                self.proxy_username, self.proxy_password or "",
            )
            local_port = await self._relay.start()
            logger.info(
                f"WhatsApp: прокси-релей 127.0.0.1:{local_port} → "
                f"{self.proxy_address}:{self.proxy_port} (авторизация через релей)"
            )
            return {"server": f"http://127.0.0.1:{local_port}"}

        # SOCKS5 с авторизацией Chromium не поддерживает в принципе — но это не
        # тот баг, что мы чиним; передаём как есть (работает без авторизации).
        proxy = {"server": f"{scheme}://{self.proxy_address}:{self.proxy_port}"}
        if self.proxy_username:
            proxy["username"] = self.proxy_username
            proxy["password"] = self.proxy_password or ""
        return proxy

    async def open(self, on_close: Optional[Callable] = None) -> bool:
        """Запустить видимое окно браузера с WhatsApp Web для этого аккаунта."""
        self._on_close = on_close
        profile_path = self._profile_path()
        profile_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"WhatsApp: прокси {self.proxy_address}:{self.proxy_port} ({self.proxy_type})")
        proxy = await self._resolve_proxy_for_chrome()

        # Не добавляем флаги из «чёрного списка» Chrome (например
        # --disable-blink-features=AutomationControlled или --no-sandbox) —
        # иначе сверху висит жёлтая плашка «неподдерживаемый флаг».
        args = [
            "--disable-notifications",
            "--lang=ru-RU",
            "--start-maximized",
        ]

        launch_kwargs = dict(
            user_data_dir=str(profile_path),
            headless=False,
            # ВАЖНО: по умолчанию Playwright сам добавляет флаг --no-sandbox
            # (chromium_sandbox=False) — из-за этого Chrome рисует жёлтую плашку.
            # Включаем настоящую песочницу, чтобы флаг не подставлялся.
            chromium_sandbox=True,
            # Убираем добавляемый Playwright флаг --enable-automation,
            # иначе Chrome пишет «управляется автоматизированным ПО».
            ignore_default_args=["--enable-automation"],
            args=args,
            proxy=proxy,
            no_viewport=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            color_scheme="light",
        )

        logger.info("WhatsApp: запуск окна браузера...")
        self._playwright = await async_playwright().start()

        # Системный Chrome надёжнее для WhatsApp Web; при отсутствии — bundled Chromium
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                channel="chrome", **launch_kwargs
            )
        except Exception as e:
            logger.warning(f"WhatsApp: системный Chrome недоступен ({e}), использую bundled Chromium")
            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        # Если пользователь закрывает окно вручную — чистим состояние
        self._context.on("close", self._handle_context_close)

        await self._page.goto(WA_WEB_URL, wait_until="domcontentloaded")
        logger.info("WhatsApp: окно открыто, загружен web.whatsapp.com")
        await asyncio.sleep(random.uniform(1, 2))
        return True

    def _handle_context_close(self, *_):
        self._context = None
        self._page = None
        # Пользователь закрыл окно сам — гасим релей И служебный драйвер Playwright.
        # Иначе после каждого закрытия крестиком в фоне остаётся висеть idle
        # node-процесс драйвера (Chrome к этому моменту уже завершился сам).
        relay = self._relay
        pw = self._playwright
        self._relay = None
        self._playwright = None
        try:
            asyncio.get_running_loop().create_task(self._async_cleanup(relay, pw))
        except RuntimeError:
            pass
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                pass

    async def _async_cleanup(self, relay, pw):
        """Фоново освободить ресурсы после закрытия окна пользователем."""
        if relay:
            try:
                await asyncio.wait_for(relay.stop(), timeout=5)
            except Exception:
                pass
        if pw:
            try:
                await asyncio.wait_for(pw.stop(), timeout=5)
            except Exception:
                pass

    async def is_logged_in(self, timeout_ms: int = 4000) -> bool:
        """Проверить, выполнен ли вход (появился список чатов #pane-side)."""
        if not self._page:
            return False
        try:
            await self._page.locator("#pane-side").wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def bring_to_front(self):
        """Вывести вкладку WhatsApp на передний план."""
        if self._page:
            try:
                await self._page.bring_to_front()
            except Exception:
                pass

    async def disconnect(self):
        """Закрыть окно браузера.

        Все шаги ограничены таймаутами: если браузер завис (например, на мёртвом
        прокси), ``ctx.close()`` может не вернуться никогда — тогда сервер не сможет
        завершиться по Ctrl+C, а порт останется занятым. ``playwright.stop()``
        принудительно гасит драйвер и дочерний Chrome даже после такого зависания.
        """
        ctx = self._context
        # Снимаем обработчик, чтобы наше же закрытие не дёргало on_close
        self._context = None
        self._page = None
        # Гасим локальный прокси-релей, если он поднимался
        relay = self._relay
        self._relay = None
        if relay:
            try:
                await asyncio.wait_for(relay.stop(), timeout=5)
            except Exception:
                pass
        try:
            if ctx:
                ctx.remove_listener("close", self._handle_context_close)
                await asyncio.wait_for(ctx.close(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("WhatsApp: ctx.close() завис (>5с) — форсирую остановку драйвера")
        except Exception as e:
            logger.warning(f"WhatsApp: ошибка закрытия контекста: {e}")
        finally:
            try:
                if self._playwright:
                    await asyncio.wait_for(self._playwright.stop(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("WhatsApp: playwright.stop() завис (>5с)")
            except Exception as e:
                logger.warning(f"WhatsApp: ошибка остановки Playwright: {e}")
            finally:
                self._playwright = None
        logger.info("WhatsApp: окно закрыто")

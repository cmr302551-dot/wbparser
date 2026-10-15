"""Authentication and account management for Telegram."""

import asyncio
import hashlib
import random
from pathlib import Path
from typing import Optional, Dict
import qrcode
from qrcode.main import QRCode

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError, FloodWaitError
from telethon.sessions import StringSession

from ..core.config import Config
from ..core.logger import get_logger
from ..core.exceptions import AuthenticationError
from .proxy import ProxyConfig
from .human_simulator import HumanSimulator

logger = get_logger(__name__)

_DEVICES = [
    ("Samsung Galaxy S21",     "Android 12"),
    ("Samsung Galaxy S22",     "Android 12"),
    ("Samsung Galaxy S23",     "Android 13"),
    ("Samsung Galaxy A52",     "Android 11"),
    ("Samsung Galaxy A53",     "Android 12"),
    ("Samsung Galaxy A72",     "Android 11"),
    ("Xiaomi Redmi Note 10",   "Android 11"),
    ("Xiaomi Redmi Note 11",   "Android 12"),
    ("Xiaomi Redmi Note 12",   "Android 13"),
    ("Xiaomi Mi 11",           "Android 11"),
    ("Xiaomi Poco X4 Pro",     "Android 12"),
    ("Huawei P40",             "Android 10"),
    ("Huawei P50",             "Android 11"),
    ("OPPO Reno 8",            "Android 12"),
    ("Realme 9 Pro",           "Android 12"),
    ("OnePlus 9",              "Android 11"),
    ("OnePlus 10 Pro",         "Android 12"),
    ("Google Pixel 6",         "Android 12"),
    ("Google Pixel 7",         "Android 13"),
    ("Vivo V23",               "Android 12"),
]

_APP_VERSIONS = [
    "10.14.4 (46049)", "10.14.5 (46054)", "11.0.0 (47004)",
    "11.1.0 (47104)", "11.2.0 (47204)", "11.3.2 (47352)",
]


def get_device_info(phone: str) -> dict:
    """Генерирует фиксированный набор device info для аккаунта на основе номера телефона."""
    seed = int(hashlib.md5(phone.encode()).hexdigest(), 16)
    rng = random.Random(seed)

    device_model, system_version = rng.choice(_DEVICES)
    app_version = rng.choice(_APP_VERSIONS)

    return {
        "device_model": device_model,
        "system_version": system_version,
        "app_version": app_version,
        "lang_code": "ru",
        "system_lang_code": "ru-RU",
    }


class TelegramAuth:
    """Handle Telegram authentication with human-like behavior."""

    def __init__(self, phone: str, proxy: Optional[ProxyConfig] = None,
                 api_id: Optional[int] = None, api_hash: Optional[str] = None):
        self.phone = phone
        self.proxy = proxy
        self.api_id = api_id or Config.TELEGRAM_API_ID
        self.api_hash = api_hash or Config.TELEGRAM_API_HASH
        self.human_simulator = HumanSimulator()

        # Session file path
        session_name = f"session_{phone.replace('+', '')}"
        self.session_path = Config.SESSIONS_DIR / f"{session_name}.session"

        # Client will be initialized in connect()
        self.client: Optional[TelegramClient] = None

    async def connect(self, log_callback=None) -> TelegramClient:
        """
        Connect to Telegram with proxy (if provided).

        Args:
            log_callback: опциональная функция (str) -> None для вывода прогресса

        Returns:
            Connected TelegramClient instance
        """
        try:
            # Prepare proxy for Telethon if provided
            client_kwargs = {}
            if self.proxy:
                logger.info(f"Подключение прокси: {self.proxy}")
                client_kwargs = self.proxy.get_client_kwargs()

            # Create client
            device_info = get_device_info(self.phone)
            self.client = TelegramClient(
                str(self.session_path),
                self.api_id,
                self.api_hash,
                **device_info,
                **client_kwargs
            )

            # Connect
            await self.client.connect()
            logger.info(f"Подключено к Telegram через {'прокси' if self.proxy else 'прямое соединение'}")

            # Check if already authorized
            if await self.client.is_user_authorized():
                logger.success(f"Аккаунт {self.phone} уже авторизован")
                me = await self.client.get_me()
                logger.info(f"Вошли как: {me.first_name} (@{me.username if me.username else 'без username'})")

                # Simulate human behavior after connection
                await self.human_simulator.initial_connection_delay(log_callback)
                return self.client

            logger.info(f"Требуется авторизация для {self.phone}")
            return self.client

        except Exception as e:
            logger.error(f"Ошибка подключения к Telegram: {e}")
            raise AuthenticationError(f"Failed to connect: {e}")

    async def authorize(self) -> TelegramClient:
        """
        Complete authorization flow with code and password.

        Returns:
            Authorized TelegramClient instance
        """
        if not self.client:
            await self.connect()

        try:
            # Check if already authorized
            if await self.client.is_user_authorized():
                logger.success(f"Аккаунт {self.phone} уже авторизован")
                return self.client

            # Send code request
            logger.info(f"Отправка кода на {self.phone}...")
            await self.client.send_code_request(self.phone)

            # Human-like delay before asking for code
            await self.human_simulator.random_delay(2, 5)

            # Request code from user
            print(f"\n{'='*50}")
            print(f"📱 На номер {self.phone} отправлен код подтверждения")
            print(f"{'='*50}")
            code = input("Введите код подтверждения: ").strip()

            # Try to sign in with code
            try:
                await self.client.sign_in(self.phone, code)
                logger.success(f"Успешная авторизация для {self.phone}")

            except SessionPasswordNeededError:
                # 2FA is enabled, ask for password
                logger.info("Требуется облачный пароль (2FA)")
                await self.human_simulator.random_delay(1, 3)

                print(f"\n{'='*50}")
                print(f"🔐 На аккаунте установлен облачный пароль")
                print(f"{'='*50}")
                password = input("Введите облачный пароль: ").strip()

                await self.client.sign_in(password=password)
                logger.success(f"Успешная авторизация с облачным паролем для {self.phone}")

            # Get user info
            me = await self.client.get_me()
            logger.info(f"Вошли как: {me.first_name} (@{me.username if me.username else 'без username'})")

            # Simulate human behavior after authorization
            await self.human_simulator.initial_connection_delay()
            await self.human_simulator.online_activity(self.client)

            return self.client

        except PhoneNumberInvalidError:
            logger.error(f"Неверный номер телефона: {self.phone}")
            raise AuthenticationError("Invalid phone number")

        except FloodWaitError as e:
            logger.error(f"FloodWait ошибка: нужно подождать {e.seconds} секунд")
            await self.human_simulator.wait_for_floodwait(e.seconds)
            raise AuthenticationError(f"FloodWait: {e.seconds} seconds")

        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
            raise AuthenticationError(f"Authorization failed: {e}")

    async def disconnect(self):
        """Disconnect from Telegram."""
        if self.client:
            await self.client.disconnect()
            logger.info(f"Отключено от Telegram ({self.phone})")

    async def get_me(self):
        """Get current user info."""
        if self.client and await self.client.is_user_authorized():
            return await self.client.get_me()
        return None

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self.client is not None and self.client.is_connected()

    async def is_authorized(self) -> bool:
        """Check if user is authorized."""
        if not self.client:
            return False
        return await self.client.is_user_authorized()

    async def authorize_with_qr(self, expected_phone: str) -> TelegramClient:
        """
        Авторизация через QR-код (сканирование с телефона).

        Args:
            expected_phone: Номер телефона для проверки после авторизации

        Returns:
            Авторизованный TelegramClient
        """
        if not self.client:
            await self.connect()

        try:
            # Проверяем что ещё не авторизованы
            if await self.client.is_user_authorized():
                logger.success(f"Аккаунт {self.phone} уже авторизован")
                return self.client

            logger.info("Запуск QR-авторизации...")

            print(f"\n{'='*60}")
            print(f"🔐 QR-АВТОРИЗАЦИЯ")
            print(f"{'='*60}")
            print(f"1. Откройте Telegram на телефоне")
            print(f"2. Перейдите в Настройки → Устройства → Подключить устройство")
            print(f"3. Отсканируйте QR-код:")
            print(f"{'='*60}\n")

            # QR-авторизация
            qr_login = await self.client.qr_login()

            # Выводим ссылку для создания QR-кода
            print(f"🔗 Ссылка для QR-кода:")
            print(f"{qr_login.url}\n")
            print(f"📱 Создайте QR-код из ссылки выше и отсканируйте его")
            print(f"   (можно использовать https://www.qr-code-generator.com/)\n")

            # Также пытаемся показать QR в консоли (опционально)
            try:
                qr = QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(qr_login.url)
                qr.make(fit=True)
                qr.print_ascii()
                print("")
            except:
                pass

            print(f"{'='*60}")
            print(f"⏳ Ожидание сканирования QR-кода...")
            print(f"{'='*60}\n")

            # Ждём сканирования (блокирующий вызов, бесконечное ожидание)
            await qr_login.wait(timeout=None)

            logger.success("✅ QR-код отсканирован! Авторизация успешна")

            # Получаем информацию о пользователе
            me = await self.client.get_me()
            user_phone = f"+{me.phone}" if not me.phone.startswith('+') else me.phone

            # Проверяем совпадение номера
            expected_clean = expected_phone.replace('+', '').replace(' ', '').replace('-', '')
            user_clean = user_phone.replace('+', '').replace(' ', '').replace('-', '')

            if user_clean != expected_clean:
                logger.error(f"❌ Ошибка: ожидался номер {expected_phone}, получен {user_phone}")
                raise AuthenticationError(
                    f"Номер не совпадает! Ожидался: {expected_phone}, получен: {user_phone}"
                )

            logger.info(f"✅ Номер телефона подтверждён: {user_phone}")
            logger.info(f"Вошли как: {me.first_name} (@{me.username if me.username else 'без username'})")

            print(f"\n{'='*60}")
            print(f"✅ АВТОРИЗАЦИЯ УСПЕШНА")
            print(f"{'='*60}")
            print(f"Имя: {me.first_name}")
            print(f"Username: @{me.username if me.username else 'не установлен'}")
            print(f"Телефон: {user_phone}")
            print(f"{'='*60}\n")

            # Имитация человеческого поведения после авторизации
            await self.human_simulator.initial_connection_delay()
            await self.human_simulator.online_activity(self.client)

            return self.client

        except asyncio.CancelledError:
            logger.info("QR-авторизация отменена")
            raise AuthenticationError("QR authorization cancelled")

        except Exception as e:
            logger.error(f"Ошибка QR-авторизации: {e}")
            raise AuthenticationError(f"QR authorization failed: {e}")


class AccountManager:
    """Manage multiple Telegram accounts."""

    def __init__(self):
        self.accounts: Dict[str, TelegramAuth] = {}

    async def add_account(self, phone: str, proxy: Optional[ProxyConfig] = None) -> TelegramAuth:
        """
        Add and authorize a new account.

        Args:
            phone: Phone number
            proxy: Optional proxy configuration

        Returns:
            TelegramAuth instance
        """
        logger.account_action(phone, "Добавление аккаунта")

        auth = TelegramAuth(phone, proxy)
        await auth.connect()

        # Authorize if needed
        if not await auth.is_authorized():
            await auth.authorize()

        self.accounts[phone] = auth
        logger.success(f"Аккаунт {phone} успешно добавлен")

        return auth

    def get_account(self, phone: str) -> Optional[TelegramAuth]:
        """Get account by phone number."""
        return self.accounts.get(phone)

    async def remove_account(self, phone: str):
        """Remove account."""
        if phone in self.accounts:
            await self.accounts[phone].disconnect()
            del self.accounts[phone]
            logger.info(f"Аккаунт {phone} удален")

    async def disconnect_all(self):
        """Disconnect all accounts."""
        for phone, auth in self.accounts.items():
            await auth.disconnect()
        self.accounts.clear()
        logger.info("Все аккаунты отключены")

    def get_all_accounts(self) -> Dict[str, TelegramAuth]:
        """Get all accounts."""
        return self.accounts.copy()

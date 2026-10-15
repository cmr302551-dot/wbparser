"""Human behavior simulation to avoid detection."""

import asyncio
import random
from typing import Optional

from ..core.config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class HumanSimulator:
    """Simulate human-like behavior in Telegram."""

    def __init__(self):
        self.config = Config

    async def random_delay(self, min_seconds: Optional[float] = None,
                          max_seconds: Optional[float] = None) -> None:
        """
        Wait for a random period to simulate human behavior.

        Args:
            min_seconds: Minimum delay in seconds (default from config)
            max_seconds: Maximum delay in seconds (default from config)
        """
        min_delay = min_seconds or self.config.MIN_ACTION_DELAY
        max_delay = max_seconds or self.config.MAX_ACTION_DELAY

        delay = random.uniform(min_delay, max_delay)
        logger.debug(f"Задержка: {delay:.2f} секунд")
        await asyncio.sleep(delay)

    async def typing_delay(self, text_length: int) -> None:
        """
        Simulate typing time based on text length.

        Args:
            text_length: Length of text being "typed"
        """
        typing_time = text_length * self.config.TYPING_SPEED
        # Add some randomness
        typing_time = typing_time * random.uniform(0.8, 1.2)

        logger.debug(f"Имитация набора текста: {typing_time:.2f} секунд")
        await asyncio.sleep(typing_time)

    async def reading_delay(self) -> None:
        """Simulate time spent reading before replying."""
        read_time = self.config.READ_SPEED * random.uniform(0.5, 1.5)
        logger.debug(f"Имитация чтения: {read_time:.2f} секунд")
        await asyncio.sleep(read_time)

    async def send_message_delay(self, min_delay: Optional[int] = None,
                                 max_delay: Optional[int] = None) -> None:
        """
        Delay between sending messages (from task configuration).

        Args:
            min_delay: Minimum delay in seconds
            max_delay: Maximum delay in seconds
        """
        if min_delay is None or max_delay is None:
            min_delay = self.config.MIN_DELAY_SECONDS
            max_delay = self.config.MAX_DELAY_SECONDS

        delay = random.randint(min_delay, max_delay)
        logger.info(f"Пауза перед следующим сообщением: {delay} секунд")
        await asyncio.sleep(delay)

    async def online_activity(self, client) -> None:
        """
        Simulate online activity to appear more human-like.

        Args:
            client: Telethon client instance
        """
        try:
            # Get dialogs to simulate checking messages
            await client.get_dialogs(limit=5)
            await self.random_delay(2, 5)

            logger.debug("Имитация онлайн активности")
        except Exception as e:
            logger.debug(f"Ошибка при имитации активности: {e}")

    async def initial_connection_delay(self, log_callback=None) -> None:
        """Delay after initial connection to look more natural."""
        delay = random.uniform(5, 15)
        logger.info(f"Начальная задержка после подключения: {delay:.2f} секунд")
        if log_callback:
            log_callback(f"⏳ Подключен, начальная задержка {delay:.1f} сек...")
        await asyncio.sleep(delay)

    def randomize_text(self, text: str) -> str:
        """
        Add minor random variations to text (spaces, punctuation).

        Args:
            text: Original text

        Returns:
            Slightly varied text
        """
        # This is a simple implementation
        # Could be extended with more sophisticated variations

        # Randomly add/remove trailing spaces
        if random.random() > 0.7:
            text = text + " "

        return text

    async def wait_for_floodwait(self, seconds: int) -> None:
        """
        Wait for FloodWait error with additional buffer.

        Args:
            seconds: Seconds to wait from FloodWait error
        """
        # Wait 20% longer than required for safety
        wait_time = int(seconds * self.config.FLOODWAIT_MULTIPLIER)
        logger.warning(f"FloodWait! Ожидание {wait_time} секунд...")

        # Log progress every minute
        for i in range(0, wait_time, 60):
            remaining = wait_time - i
            logger.info(f"FloodWait: осталось ~{remaining // 60} минут")
            await asyncio.sleep(min(60, remaining))

    async def simulate_read_chat(self, client, chat) -> None:
        """
        Simulate reading a chat before sending a message.

        Args:
            client: Telethon client
            chat: Chat entity
        """
        try:
            # Get few messages to simulate reading
            await client.get_messages(chat, limit=3)
            await self.reading_delay()
        except Exception as e:
            logger.debug(f"Не удалось прочитать чат: {e}")

    def get_random_delay_range(self) -> tuple[int, int]:
        """Get random delay range for message sending."""
        return (
            self.config.MIN_DELAY_SECONDS,
            self.config.MAX_DELAY_SECONDS
        )

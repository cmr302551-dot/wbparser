"""High-level Telegram client manager."""

from pathlib import Path
from typing import Optional, List

from ..core.database import Database
from ..core.logger import get_logger
from .auth import TelegramAuth
from .proxy import ProxyConfig
from .collectors import (
    ChatCollector,
    ChannelCollector,
    CustomDatabaseCollector,
    CollectedUser
)
from .sender import MessageSender

logger = get_logger(__name__)


class TelegramClientManager:
    """High-level manager for Telegram operations."""

    def __init__(self, phone: str, db: Database, proxy: Optional[ProxyConfig] = None,
                 api_id: Optional[int] = None, api_hash: Optional[str] = None):
        self.phone = phone
        self.db = db
        self.proxy = proxy

        # Initialize auth
        self.auth = TelegramAuth(phone, proxy, api_id=api_id, api_hash=api_hash)
        self.account_id: Optional[int] = None

    async def initialize(self):
        """Connect and authorize account."""
        # Connect
        await self.auth.connect()

        # Check authorization — never call authorize() as it uses terminal input()
        if not await self.auth.is_authorized():
            raise Exception(f"Аккаунт {self.phone} не авторизован. Авторизуйтесь через бот.")

        # Get or create account in database
        account = await self.db.get_account_by_phone(self.phone)
        if not account:
            proxy_id = None
            if self.proxy:
                # Save proxy to database
                proxy_id = await self.db.add_proxy(
                    address=self.proxy.address,
                    port=self.proxy.port,
                    username=self.proxy.username,
                    password=self.proxy.password,
                    proxy_type=self.proxy.proxy_type
                )

            self.account_id = await self.db.add_account(self.phone, proxy_id)
        else:
            self.account_id = account['id']

        logger.success(f"Клиент инициализирован: {self.phone}")

    async def disconnect(self):
        """Disconnect client."""
        await self.auth.disconnect()

    @property
    def client(self):
        """Get Telethon client."""
        return self.auth.client

    # ============= COLLECTORS =============

    def get_chat_collector(self) -> ChatCollector:
        """Get chat collector instance."""
        return ChatCollector(self.client, self.db)

    def get_channel_collector(self) -> ChannelCollector:
        """Get channel collector instance."""
        return ChannelCollector(self.client, self.db)

    def get_custom_collector(self) -> CustomDatabaseCollector:
        """Get custom database collector instance."""
        return CustomDatabaseCollector(self.client, self.db)

    # ============= SENDER =============

    def get_sender(self) -> MessageSender:
        """Get message sender instance."""
        return MessageSender(self.client, self.db, self.account_id)

    # ============= CONVENIENCE METHODS =============

    async def send_to_users(
        self,
        users: List[CollectedUser],
        message_template: str,
        image_path: Optional[Path] = None,
        min_delay: int = 10,
        max_delay: int = 30,
        max_messages: Optional[int] = None,
        task_name: str = "Рассылка",
        sellers_data: Optional[dict] = None,
        message2: Optional[str] = None
    ) -> dict:
        """
        Convenience method to send messages to users.

        Args:
            users: List of users
            message_template: Message template
            image_path: Optional image
            min_delay: Min delay between messages
            max_delay: Max delay between messages
            max_messages: Max messages to send
            task_name: Task name for logging
            sellers_data: Dictionary mapping username to seller data

        Returns:
            Statistics dictionary
        """
        # Create task in database
        task_id = await self.db.create_task(
            task_name=task_name,
            account_id=self.account_id,
            total_recipients=len(users) if not max_messages else min(len(users), max_messages)
        )

        # Send messages
        sender = self.get_sender()
        stats = await sender.send_bulk(
            users=users,
            message_template=message_template,
            image_path=image_path,
            min_delay=min_delay,
            max_delay=max_delay,
            max_messages=max_messages,
            task_id=task_id,
            sellers_data=sellers_data,
            message2=message2
        )

        # Update task status
        await self.db.update_task_status(task_id, 'completed')

        return stats

    async def send_to_chats(
        self,
        chat_links: List[str],
        message_template: str,
        image_path: Optional[Path] = None,
        min_delay: int = 10,
        max_delay: int = 30,
        cycles: int = 1,
        cycle_delay: int = 1800,
        task_name: str = "Рассылка в чаты"
    ) -> dict:
        """
        Convenience method to send messages to chats.

        Args:
            chat_links: List of chat links
            message_template: Message template (with spintax, without {name})
            image_path: Optional image
            min_delay: Min delay between messages
            max_delay: Max delay between messages
            cycles: Number of cycles
            cycle_delay: Delay between cycles (seconds)
            task_name: Task name for logging

        Returns:
            Statistics dictionary
        """
        # Create task in database
        task_id = await self.db.create_task(
            task_name=task_name,
            account_id=self.account_id,
            total_recipients=len(chat_links) * cycles
        )

        # Send messages
        sender = self.get_sender()
        stats = await sender.send_to_chats_bulk(
            chat_links=chat_links,
            message_template=message_template,
            image_path=image_path,
            min_delay=min_delay,
            max_delay=max_delay,
            cycles=cycles,
            cycle_delay=cycle_delay,
            task_id=task_id
        )

        # Update task status
        await self.db.update_task_status(task_id, 'completed')

        return stats

    async def get_me(self):
        """Get current user info."""
        return await self.auth.get_me()

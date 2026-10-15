"""Base collector class."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from telethon import TelegramClient
from telethon.tl.types import User

from ...core.logger import get_logger
from ...core.database import Database

logger = get_logger(__name__)


class CollectedUser:
    """Represents a collected user."""

    def __init__(
        self,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        source_name: Optional[str] = None,
        original_identifier: Optional[str] = None,
        access_hash: Optional[int] = None
    ):
        self.user_id = user_id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name
        self.phone = phone
        self.source_name = source_name  # Название чата/канала откуда собран
        self.original_identifier = original_identifier  # Оригинальный идентификатор (t.me/+... или username)
        self.access_hash = access_hash

    @classmethod
    def from_telethon_user(cls, user: User, source_name: Optional[str] = None) -> 'CollectedUser':
        """Create CollectedUser from Telethon User object."""
        return cls(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            phone=getattr(user, 'phone', None),
            source_name=source_name,
            access_hash=getattr(user, 'access_hash', None) if not user.username else None
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'user_id': self.user_id,
            'username': self.username,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'phone': self.phone,
            'source_name': self.source_name
        }

    def get_display_name(self) -> str:
        """Get display name for user."""
        if self.username:
            return f"@{self.username}"
        elif self.first_name:
            full_name = self.first_name
            if self.last_name:
                full_name += f" {self.last_name}"
            return full_name
        else:
            return f"ID:{self.user_id}"

    def __str__(self) -> str:
        return self.get_display_name()

    def __repr__(self) -> str:
        return f"CollectedUser({self.user_id}, {self.get_display_name()})"


class BaseCollector(ABC):
    """Base class for all audience collectors."""

    def __init__(self, client: TelegramClient, db: Database):
        """
        Initialize collector.

        Args:
            client: Authenticated Telethon client
            db: Database instance
        """
        self.client = client
        self.db = db
        self.collected_users: List[CollectedUser] = []

    @abstractmethod
    async def collect(self, **kwargs) -> List[CollectedUser]:
        """
        Collect users based on specific criteria.

        Returns:
            List of collected users
        """
        pass

    async def save_to_database(self, task_id: int, collection_type: str, source: str):
        """
        Save collected users to database.

        Args:
            task_id: Task ID
            collection_type: Type of collection
            source: Source (chat/channel name)
        """
        for user in self.collected_users:
            await self.db.save_collected_user(
                task_id=task_id,
                collection_type=collection_type,
                source=source,
                user_id=user.user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )

        logger.info(f"Сохранено {len(self.collected_users)} пользователей в базу данных")

    def get_collected_users(self) -> List[CollectedUser]:
        """Get list of collected users."""
        return self.collected_users.copy()

    def clear(self):
        """Clear collected users."""
        self.collected_users.clear()

    def add_user(self, user: CollectedUser):
        """Add user to collected list (avoiding duplicates)."""
        if not user.username:
            return
        if not any(u.user_id == user.user_id for u in self.collected_users):
            self.collected_users.append(user)

    def get_count(self) -> int:
        """Get count of collected users."""
        return len(self.collected_users)

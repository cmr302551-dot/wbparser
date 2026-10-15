"""Filters for audience collection."""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Union, List

from telethon.tl.types import User


class MessageFilterType(Enum):
    """Message filter types."""
    COUNT = "count"  # Filter by number of messages
    DATE = "date"  # Filter by message date
    BOTH = "both"  # Filter by both count AND date


class MessageCountFilter(Enum):
    """Message count filter options."""
    FROM_1 = 1
    FROM_5 = 5
    FROM_10 = 10
    FROM_50 = 50
    FROM_100 = 100
    FROM_250 = 250
    FROM_500 = 500
    FROM_1000 = 1000
    FROM_1500 = 1500


class CommentCountFilter(Enum):
    """Comment count filter options (for channels)."""
    FROM_1 = 1
    FROM_10 = 10
    FROM_50 = 50
    FROM_100 = 100
    FROM_250 = 250
    FROM_500 = 500


class DateFilter(Enum):
    """Date filter options."""
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    THREE_MONTHS = "three_months"
    YEAR = "year"
    ALL = "all"


class OnlineStatusFilter(Enum):
    """Online status filter options."""
    WAS_RECENTLY = "was_recently"  # Recently online
    WAS_THIS_WEEK = "was_this_week"  # Online this week
    WAS_THIS_MONTH = "was_this_month"  # Online this month
    BEEN_A_LONG_TIME_AGO = "been_a_long_time_ago"  # Long time ago
    ALL = "all"  # All users


class CollectionType(Enum):
    """Collection type for chats."""
    PARTICIPANTS = "participants"  # All participants
    ACTIVE_USERS = "active_users"  # Users who posted messages


class MessageFilter:
    """Filter messages based on count and/or date."""

    def __init__(
        self,
        filter_type: MessageFilterType = MessageFilterType.BOTH,
        count_filter: Optional[Union[MessageCountFilter, CommentCountFilter]] = None,
        date_filters: Optional[List[DateFilter]] = None,
        status_filter: Optional[Union[OnlineStatusFilter, List[OnlineStatusFilter]]] = OnlineStatusFilter.ALL,
        override_min_count: Optional[int] = None,
    ):
        """
        Initialize message filter.

        Args:
            filter_type: Type of filter (count, date, or both) - deprecated, always uses BOTH
            count_filter: Minimum message count (always applied if specified)
            date_filters: Date range filters (always applied if specified)
            status_filter: Online status filter — single value or list (OR-логика)
            override_min_count: Exact integer minimum count, bypasses count_filter enum rounding
        """
        # ALWAYS use BOTH mode now - filter_type kept for backward compatibility
        self.filter_type = MessageFilterType.BOTH
        self.count_filter = count_filter
        self.override_min_count = override_min_count
        self.date_filters = date_filters or [DateFilter.ALL]
        # Нормализуем status_filter в список для OR-логики
        if isinstance(status_filter, list):
            self.status_filters = status_filter if status_filter else [OnlineStatusFilter.ALL]
        else:
            self.status_filters = [status_filter or OnlineStatusFilter.ALL]

    def get_date_range(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """
        Get date range based on date filters.

        Returns:
            Tuple of (start_date, end_date)
        """
        # Use timezone-aware datetime to match Telegram's message dates
        # Используем московское время (UTC+3) для определения "сегодня"
        MSK = timezone(timedelta(hours=3))
        now_msk = datetime.now(MSK)
        now = datetime.now(timezone.utc)
        earliest_date = None

        for date_filter in self.date_filters:
            if date_filter == DateFilter.TODAY:
                # Начало сегодняшнего дня по МСК, конвертированное в UTC
                start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
                start = start_msk.astimezone(timezone.utc)
            elif date_filter == DateFilter.WEEK:
                start = now - timedelta(days=7)
            elif date_filter == DateFilter.MONTH:
                start = now - timedelta(days=30)
            elif date_filter == DateFilter.THREE_MONTHS:
                start = now - timedelta(days=90)
            elif date_filter == DateFilter.YEAR:
                start = now - timedelta(days=365)
            else:  # ALL
                return None, None

            if earliest_date is None or start < earliest_date:
                earliest_date = start

        return earliest_date, now

    def get_min_message_count(self) -> int:
        """Get minimum message count."""
        if self.override_min_count is not None:
            return self.override_min_count
        if self.count_filter:
            return self.count_filter.value
        return 0

    def check_user_status(self, user: User) -> bool:
        """
        Check if user matches the online status filter.
        Поддерживает OR-логику: пользователь проходит если его статус
        совпадает с ЛЮБЫМ из выбранных фильтров.

        Args:
            user: Telethon User object

        Returns:
            True if user matches any of the selected filters
        """
        if OnlineStatusFilter.ALL in self.status_filters:
            return True

        from telethon.tl.types import (
            UserStatusOnline,
            UserStatusRecently,
            UserStatusLastWeek,
            UserStatusLastMonth,
            UserStatusOffline
        )

        # Определяем к какому фильтру относится статус пользователя,
        # затем проверяем есть ли этот фильтр в выбранных
        sf = self.status_filters

        if not hasattr(user, 'status') or user.status is None:
            return OnlineStatusFilter.BEEN_A_LONG_TIME_AGO in sf

        if isinstance(user.status, UserStatusOnline):
            return OnlineStatusFilter.WAS_RECENTLY in sf

        if isinstance(user.status, UserStatusRecently):
            return OnlineStatusFilter.WAS_RECENTLY in sf

        if isinstance(user.status, UserStatusLastWeek):
            return OnlineStatusFilter.WAS_THIS_WEEK in sf

        if isinstance(user.status, UserStatusLastMonth):
            return OnlineStatusFilter.WAS_THIS_MONTH in sf

        if isinstance(user.status, UserStatusOffline):
            if hasattr(user.status, 'was_online'):
                now = datetime.now(timezone.utc)
                days_ago = (now - user.status.was_online).days
                if days_ago == 0:
                    return OnlineStatusFilter.WAS_RECENTLY in sf
                elif days_ago <= 7:
                    return OnlineStatusFilter.WAS_THIS_WEEK in sf
                elif days_ago <= 30:
                    return OnlineStatusFilter.WAS_THIS_MONTH in sf
                else:
                    return OnlineStatusFilter.BEEN_A_LONG_TIME_AGO in sf
            return OnlineStatusFilter.BEEN_A_LONG_TIME_AGO in sf

        return OnlineStatusFilter.BEEN_A_LONG_TIME_AGO in sf


def parse_chat_link(link: str) -> tuple[str, Optional[int]]:
    """
    Parse chat link to extract username and topic ID (if present).

    Форматы ссылок:
    - https://t.me/chatname/123 (публичный с темой)
    - https://t.me/c/1234567890/123 (приватный с темой)
    - https://t.me/+HASH (приватная invite-ссылка)
    - https://t.me/joinchat/HASH (приватная invite-ссылка, старый формат)
    - @chatname (публичный без темы)
    - t.me/chatname (публичный без темы)

    Args:
        link: Chat link in various formats

    Returns:
        Tuple (username/chat_id/invite_link, topic_id)
        topic_id будет None если это не тема
    """
    original_link = link.strip()
    topic_id = None

    # Remove @ if present
    if link.startswith('@'):
        return link[1:], None

    # ВАЖНО: Приватные invite-ссылки возвращаем целиком!
    # Формат: https://t.me/+HASH или https://t.me/joinchat/HASH
    if ('t.me/+' in link or 't.me/joinchat/' in link or
        'telegram.me/+' in link or 'telegram.me/joinchat/' in link):
        # Возвращаем полную ссылку для invite-ссылок
        return original_link, None

    # Extract from t.me links
    if 't.me/' in link or 'telegram.me/' in link:
        # Удаляем протокол и домен
        if 'https://' in link:
            link = link.replace('https://', '')
        if 'http://' in link:
            link = link.replace('http://', '')

        # Разбиваем на части
        parts = link.split('/')

        # Проверяем формат: t.me/c/CHAT_ID/TOPIC_ID
        if len(parts) >= 4 and parts[1] == 'c':
            # Приватный чат с topic ID
            chat_id = parts[2]
            topic_id = int(parts[3]) if parts[3].isdigit() else None
            return f"-100{chat_id}", topic_id

        # Проверяем формат: t.me/CHATNAME/TOPIC_ID
        elif len(parts) >= 3:
            chatname = parts[1]
            potential_topic = parts[2]

            # Проверяем, является ли последняя часть числом (topic ID)
            if potential_topic.isdigit():
                topic_id = int(potential_topic)
                return chatname, topic_id
            else:
                # Это не topic, а какая-то другая ссылка
                return chatname, None

        # Обычный формат: t.me/CHATNAME
        elif len(parts) >= 2:
            return parts[1].split('?')[0], None

    # Remove query parameters
    if '?' in link:
        link = link.split('?')[0]

    return link.strip(), None

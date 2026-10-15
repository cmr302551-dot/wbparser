"""Channel audience collector."""

from typing import List, Dict
from collections import defaultdict

from telethon.errors import FloodWaitError
from telethon.tl.types import User, Message

from ...core.logger import get_logger
from .base import BaseCollector, CollectedUser
from .filters import (
    MessageFilter,
    parse_chat_link
)
from ..human_simulator import HumanSimulator

logger = get_logger(__name__)


class ChannelCollector(BaseCollector):
    """Collect users from Telegram channels (from comments)."""

    def __init__(self, client, db):
        super().__init__(client, db)
        self.human_simulator = HumanSimulator()

    async def collect_commenters(self, channel_username: str, message_filter: MessageFilter) -> List[CollectedUser]:
        """
        Collect users who commented in a channel.

        Args:
            channel_username: Channel username
            message_filter: Filter with comment count/date and status filters

        Returns:
            List of collected users
        """
        logger.info(f"Сбор комментаторов из канала @{channel_username}")

        try:
            entity = await self.client.get_entity(channel_username)

            # Определяем идентификатор для {where}
            if getattr(entity, 'username', None):
                channel_name = f"@{entity.username}"
            elif isinstance(channel_username, str) and channel_username.startswith('http'):
                channel_name = channel_username
            else:
                channel_name = f"@{channel_username}"

            # Получаем диапазон дат для фильтрации постов
            start_date, end_date = message_filter.get_date_range()
            if start_date:
                logger.info(f"Фильтр по дате постов: с {start_date} по {end_date}")
            else:
                logger.info("Фильтр по дате: все посты")
            min_count = message_filter.get_min_message_count()
            if min_count > 0:
                logger.info(f"Минимум комментариев: {min_count}")

            # Collect posts with comments
            user_comment_counts: Dict[int, User] = {}
            comment_counts: Dict[int, int] = defaultdict(int)

            logger.info("Сбор постов с комментариями...")

            post_count = 0
            total_comments = 0

            # Получаем ID текущего аккаунта
            me = await self.client.get_me()
            my_user_id = me.id

            # Iterate through channel posts
            # limit=None: итерируем все посты, обрываем по дате (не по количеству)
            async for post in self.client.iter_messages(
                entity,
                limit=None,
                offset_date=end_date if end_date else None
            ):
                # Check date filter
                if start_date and post.date < start_date:
                    break

                post_count += 1

                # Check if post has comments
                if not hasattr(post, 'replies') or not post.replies or post.replies.replies == 0:
                    continue

                try:
                    # Get comments for this post
                    comment_in_post = 0
                    async for comment in self.client.iter_messages(
                        entity,
                        reply_to=post.id,
                        limit=None
                    ):
                        total_comments += 1
                        comment_in_post += 1

                        # Only count user comments (not from bots or channels)
                        if comment.sender and isinstance(comment.sender, User) and not comment.sender.bot:
                            user_id = comment.sender.id

                            # Исключаем свой аккаунт
                            if user_id == my_user_id:
                                continue

                            comment_counts[user_id] += 1

                            # Store user object
                            if user_id not in user_comment_counts:
                                user_comment_counts[user_id] = comment.sender

                    # Delay between posts
                    await self.human_simulator.random_delay(1, 3)

                except Exception as e:
                    logger.debug(f"Ошибка при получении комментариев к посту {post.id}: {e}")
                    continue

            logger.info(f"Обработано {post_count} постов, найдено {total_comments} комментариев")
            logger.info(f"Уникальных комментаторов: {len(user_comment_counts)}")

            # Filter by comment count
            for user_id, user in user_comment_counts.items():
                count = comment_counts[user_id]

                if count >= min_count:
                    # Check online status filter
                    if message_filter.check_user_status(user):
                        if not user.username:
                            continue
                        collected_user = CollectedUser.from_telethon_user(user, source_name=channel_name)
                        self.add_user(collected_user)
                        logger.debug(f"✓ {collected_user} ({count} комментариев)")

            logger.success(f"Собрано {self.get_count()} комментаторов из @{channel_username}")
            return self.get_collected_users()

        except FloodWaitError as e:
            logger.warning(f"FloodWait! Ожидание {e.seconds} секунд...")
            await self.human_simulator.wait_for_floodwait(e.seconds)
            # Retry after waiting
            return await self.collect_commenters(channel_username, message_filter)
        except Exception as e:
            logger.error(f"❌ Ошибка при сборе комментаторов из @{channel_username}: {e}")
            return []

    async def collect(
        self,
        channel_links: List[str],
        message_filter: MessageFilter
    ) -> List[CollectedUser]:
        """
        Collect users from multiple channels.

        Args:
            channel_links: List of channel links/usernames
            message_filter: Message filter for comments

        Returns:
            List of collected users from all channels
        """
        self.clear()

        for link in channel_links:
            channel_username, topic_id = parse_chat_link(link.strip())

            if not channel_username:
                continue

            logger.info(f"{'='*60}")
            logger.info(f"Обработка канала: @{channel_username}")
            logger.info(f"{'='*60}")

            try:
                await self.collect_commenters(channel_username, message_filter)

                # Human-like delay between channels
                await self.human_simulator.random_delay(5, 10)

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке @{channel_username}: {e}")
                continue

        logger.info(f"{'='*60}")
        logger.success(f"ИТОГО собрано: {self.get_count()} уникальных комментаторов")
        logger.info(f"{'='*60}")

        return self.get_collected_users()

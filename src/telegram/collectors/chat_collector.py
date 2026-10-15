"""Chat audience collector."""

import asyncio
from typing import List, Dict
from collections import defaultdict

from telethon.errors import ChatAdminRequiredError, FloodWaitError
from telethon.tl.types import (
    User,
    ChatParticipantAdmin, ChatParticipantCreator,
    ChannelParticipantAdmin, ChannelParticipantCreator,
)

from ...core.logger import get_logger
from .base import BaseCollector, CollectedUser
from .filters import (
    CollectionType,
    MessageFilter,
    MessageFilterType,
    parse_chat_link
)
from ..human_simulator import HumanSimulator

logger = get_logger(__name__)


class ChatCollector(BaseCollector):
    """Collect users from Telegram chats."""

    def __init__(self, client, db):
        super().__init__(client, db)
        self.human_simulator = HumanSimulator()

    async def collect_participants(self, chat_username: str, message_filter: MessageFilter, topic_id: int = None, exclude_admins: bool = False) -> List[CollectedUser]:
        """
        Collect all participants from a chat.

        Args:
            chat_username: Chat username
            message_filter: Filter to apply (only status filter used here)
            topic_id: Topic ID (for forum chats), None for regular chats

        Returns:
            List of collected users
        """
        topic_info = f" (тема {topic_id})" if topic_id else ""
        logger.info(f"Сбор участников из чата @{chat_username}{topic_info}")

        try:
            entity = await self.client.get_entity(chat_username)

            # Определяем идентификатор для {where}
            if topic_id:
                if getattr(entity, 'username', None):
                    chat_name = f"https://t.me/{entity.username}/{topic_id}"
                elif isinstance(chat_username, str) and chat_username.startswith('-100'):
                    chat_name = f"https://t.me/c/{chat_username[4:]}/{topic_id}"
                else:
                    chat_name = f"https://t.me/{chat_username}/{topic_id}"
            elif getattr(entity, 'username', None):
                chat_name = f"@{entity.username}"
            elif isinstance(chat_username, str) and chat_username.startswith('http'):
                chat_name = chat_username
            else:
                chat_name = f"@{chat_username}"

            # Get all participants
            logger.info("Получение списка участников...")
            participants = await self.client.get_participants(entity, limit=None)

            logger.info(f"Найдено {len(participants)} участников")

            # Получаем ID текущего аккаунта
            me = await self.client.get_me()
            my_user_id = me.id

            # Filter by online status
            filtered_count = 0
            for participant in participants:
                if isinstance(participant, User) and not participant.bot:
                    # Исключаем свой аккаунт
                    if participant.id == my_user_id:
                        continue

                    # Исключаем администраторов и создателей
                    if exclude_admins:
                        p = getattr(participant, 'participant', None)
                        if isinstance(p, (ChatParticipantAdmin, ChatParticipantCreator,
                                          ChannelParticipantAdmin, ChannelParticipantCreator)):
                            continue

                    if message_filter.check_user_status(participant):
                        if not participant.username:
                            continue
                        collected_user = CollectedUser.from_telethon_user(participant, source_name=chat_name)
                        self.add_user(collected_user)
                        filtered_count += 1

            logger.success(f"Собрано {filtered_count} участников из @{chat_username}")
            return self.get_collected_users()

        except ChatAdminRequiredError:
            logger.error(f"❌ Нет доступа к списку участников @{chat_username} (требуются права админа)")
            return []
        except FloodWaitError as e:
            logger.warning(f"FloodWait! Ожидание {e.seconds} секунд...")
            await self.human_simulator.wait_for_floodwait(e.seconds)
            # Retry after waiting
            return await self.collect_participants(chat_username, message_filter)
        except Exception as e:
            logger.error(f"❌ Ошибка при сборе участников из @{chat_username}: {e}")
            return []

    async def collect_active_users(self, chat_username: str, message_filter: MessageFilter, topic_id: int = None, exclude_admins: bool = False) -> List[CollectedUser]:
        """
        Collect users who posted messages in a chat.

        Args:
            chat_username: Chat username
            message_filter: Filter with message count/date and status filters
            topic_id: Topic ID (for forum chats), None for regular chats

        Returns:
            List of collected users
        """
        topic_info = f" (тема {topic_id})" if topic_id else ""
        logger.info(f"Сбор активных пользователей из чата @{chat_username}{topic_info}")

        try:
            entity = await self.client.get_entity(chat_username)

            # Определяем идентификатор для {where}
            if topic_id:
                if getattr(entity, 'username', None):
                    chat_name = f"https://t.me/{entity.username}/{topic_id}"
                elif isinstance(chat_username, str) and chat_username.startswith('-100'):
                    chat_name = f"https://t.me/c/{chat_username[4:]}/{topic_id}"
                else:
                    chat_name = f"https://t.me/{chat_username}/{topic_id}"
            elif getattr(entity, 'username', None):
                chat_name = f"@{entity.username}"
            elif isinstance(chat_username, str) and chat_username.startswith('http'):
                chat_name = chat_username
            else:
                chat_name = f"@{chat_username}"

            # Get both filters
            start_date, end_date = message_filter.get_date_range()
            min_count = message_filter.get_min_message_count()

            # Log filters
            if start_date:
                logger.info(f"Фильтр по дате: с {start_date} по {end_date}")
            if min_count > 0:
                logger.info(f"Фильтр по количеству сообщений: от {min_count}")
            if topic_id:
                logger.info(f"Сбор только из темы с ID: {topic_id}")

            # Получаем список ID администраторов (если нужно исключить)
            admin_ids: set = set()
            if exclude_admins:
                try:
                    from telethon.tl.types import ChannelParticipantsAdmins
                    admins = await self.client.get_participants(entity, filter=ChannelParticipantsAdmins())
                    admin_ids = {admin.id for admin in admins}
                    logger.info(f"Найдено {len(admin_ids)} администраторов для исключения")
                except Exception as e:
                    logger.warning(f"Не удалось получить список администраторов: {e}")

            # Collect messages
            user_message_counts: Dict[int, User] = {}
            message_counts: Dict[int, int] = defaultdict(int)

            # Получаем ID текущего аккаунта
            me = await self.client.get_me()
            my_user_id = me.id

            logger.info("Сбор сообщений...")

            # Iterate through messages
            # Для форумов (топиков) используем reply_to для фильтрации
            processed_messages = 0
            async for message in self.client.iter_messages(
                entity,
                limit=None if start_date else 5000,
                offset_date=end_date if end_date else None,
                reply_to=topic_id if topic_id else None
            ):
                # Check date filter
                if start_date and message.date < start_date:
                    break

                processed_messages += 1

                # Пауза каждые 30 сообщений чтобы не получить FloodWait
                if processed_messages % 30 == 0:
                    await asyncio.sleep(1.5)

                # Only count user messages (not from bots or channels)
                if message.sender and isinstance(message.sender, User) and not message.sender.bot:
                    user_id = message.sender.id

                    # Исключаем свой аккаунт
                    if user_id == my_user_id:
                        continue

                    # Исключаем администраторов
                    if exclude_admins and user_id in admin_ids:
                        continue

                    message_counts[user_id] += 1

                    # Store user object
                    if user_id not in user_message_counts:
                        user_message_counts[user_id] = message.sender

            logger.info(f"Обработано {processed_messages} сообщений от {len(user_message_counts)} пользователей")

            # Filter by message count (already got min_count above)
            for user_id, user in user_message_counts.items():
                count = message_counts[user_id]

                if count >= min_count:
                    # Check online status filter
                    if message_filter.check_user_status(user):
                        if not user.username:
                            continue
                        collected_user = CollectedUser.from_telethon_user(user, source_name=chat_name)
                        self.add_user(collected_user)
                        logger.debug(f"✓ {collected_user} ({count} сообщений)")

            logger.success(f"Собрано {self.get_count()} активных пользователей из @{chat_username}{topic_info}")
            return self.get_collected_users()

        except FloodWaitError as e:
            logger.warning(f"FloodWait! Ожидание {e.seconds} секунд...")
            await self.human_simulator.wait_for_floodwait(e.seconds)
            # Retry after waiting
            return await self.collect_active_users(chat_username, message_filter, topic_id)
        except Exception as e:
            logger.error(f"❌ Ошибка при сборе активных пользователей из @{chat_username}{topic_info}: {e}")
            return []

    async def collect(
        self,
        chat_links: List[str],
        collection_type: CollectionType = CollectionType.PARTICIPANTS,
        message_filter: MessageFilter = None
    ) -> List[CollectedUser]:
        """
        Collect users from multiple chats.

        Args:
            chat_links: List of chat links/usernames
            collection_type: Type of collection (participants or active_users)
            message_filter: Message filter (required for active_users)

        Returns:
            List of collected users from all chats
        """
        self.clear()

        for link in chat_links:
            chat_username, topic_id = parse_chat_link(link.strip())

            if not chat_username:
                continue

            topic_info = f" (тема {topic_id})" if topic_id else ""
            logger.info(f"{'='*60}")
            logger.info(f"Обработка чата: @{chat_username}{topic_info}")
            logger.info(f"{'='*60}")

            try:
                if collection_type == CollectionType.PARTICIPANTS:
                    await self.collect_participants(chat_username, message_filter or MessageFilter(MessageFilterType.COUNT), topic_id)
                else:  # ACTIVE_USERS
                    if not message_filter:
                        logger.error("MessageFilter required for active users collection")
                        continue
                    await self.collect_active_users(chat_username, message_filter, topic_id)

                # Human-like delay between chats
                await self.human_simulator.random_delay(5, 10)

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке @{chat_username}: {e}")
                continue

        logger.info(f"{'='*60}")
        logger.success(f"ИТОГО собрано: {self.get_count()} уникальных пользователей")
        logger.info(f"{'='*60}")

        return self.get_collected_users()

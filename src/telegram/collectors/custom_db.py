"""Custom database collector - for manually provided chats and usernames."""

from typing import List, Optional

from telethon.errors import UsernameInvalidError, FloodWaitError
from telethon.tl.types import User, Chat, Channel

from ...core.logger import get_logger
from ...core.exceptions import CollectorError
from .base import BaseCollector, CollectedUser
from .filters import parse_chat_link
from ..human_simulator import HumanSimulator

logger = get_logger(__name__)


class CustomDatabaseCollector(BaseCollector):
    """Collect users from manually provided lists."""

    def __init__(self, client, db):
        super().__init__(client, db)
        self.human_simulator = HumanSimulator()

    async def collect_from_usernames(self, usernames: List[str]) -> List[CollectedUser]:
        """
        Collect users from a list of usernames.

        Args:
            usernames: List of usernames (with or without @)

        Returns:
            List of collected users
        """
        logger.info(f"Сбор пользователей из списка логинов ({len(usernames)} логинов)")

        self.clear()

        # Получаем ID текущего аккаунта
        me = await self.client.get_me()
        my_user_id = me.id

        for username in usernames:
            # Clean username
            clean_username = username.strip().lstrip('@')

            if not clean_username:
                continue

            try:
                user = await self.client.get_entity(clean_username)

                if isinstance(user, User):
                    # Исключаем свой аккаунт
                    if user.id == my_user_id:
                        logger.info(f"⊘ Пропущен свой аккаунт: @{clean_username}")
                        continue

                    collected_user = CollectedUser.from_telethon_user(user)
                    collected_user.original_identifier = clean_username
                    self.add_user(collected_user)
                    logger.info(f"✓ Собран пользователь: {collected_user}")
                else:
                    logger.warning(f"⚠ {clean_username} не является пользователем")

                # Human-like delay
                await self.human_simulator.random_delay(1, 3)

            except UsernameInvalidError:
                logger.warning(f"❌ Неверный логин: {clean_username}")
            except FloodWaitError as e:
                logger.warning(f"FloodWait! Ожидание {e.seconds} секунд...")
                await self.human_simulator.wait_for_floodwait(e.seconds)
            except Exception as e:
                logger.error(f"❌ Ошибка при сборе {clean_username}: {e}")

        logger.success(f"Собрано {self.get_count()} пользователей из списка логинов")
        return self.get_collected_users()

    async def collect_from_chats(
        self,
        chat_links: List[str],
        account_id: int,
        task_type: str = None,
        database_name: str = None,
        task_params: dict = None
    ) -> List[str]:
        """
        Collect chat links and validate accessibility.
        Works with public/private chats. Automatically joins via invite links and sends join requests.

        Args:
            chat_links: List of chat links (public usernames or private invite links)
            account_id: Account ID for tracking join requests

        Returns:
            List of accessible chat usernames/IDs
        """
        logger.info(f"Обработка списка ({len(chat_links)} ссылок)")

        accessible_chats = []
        channel_count = 0
        chat_count = 0
        self.clear()

        for link in chat_links:
            link = link.strip()

            try:
                logger.info(f"⚙ Обработка ссылки: {link}")

                # Парсим ссылку чтобы извлечь username/chat_id и topic_id
                from .filters import parse_chat_link
                chat_identifier, topic_id = parse_chat_link(link)

                # Проверяем, является ли это invite-ссылкой
                is_invite_link = ('t.me/+' in chat_identifier or
                                 't.me/joinchat/' in chat_identifier or
                                 'telegram.me/+' in chat_identifier or
                                 'telegram.me/joinchat/' in chat_identifier)

                # Пробуем получить entity
                entity = None
                join_requested = False

                if is_invite_link:
                    # Для invite-ссылок сразу пробуем присоединиться
                    from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest

                    # Извлекаем hash из ссылки
                    invite_hash = chat_identifier.split('/')[-1]
                    if invite_hash.startswith('+'):
                        invite_hash = invite_hash[1:]

                    try:
                        # Пробуем присоединиться
                        logger.info(f"🔗 Попытка присоединения к чату...")
                        result = await self.client(ImportChatInviteRequest(hash=invite_hash))

                        # Получаем entity из результата
                        if hasattr(result, 'chats') and result.chats:
                            entity = result.chats[0]
                            logger.success(f"✅ Успешно присоединились к чату")

                    except Exception as join_error:
                        error_msg = str(join_error)

                        # Если уже участник или FloodWait - используем CheckChatInviteRequest чтобы получить entity
                        if 'USER_ALREADY_PARTICIPANT' in error_msg or 'already a participant' in error_msg.lower() or 'wait of' in error_msg.lower():
                            if 'wait of' in error_msg.lower():
                                logger.info(f"ℹ️ FloodWait - используем альтернативный метод получения entity")
                            else:
                                logger.info(f"ℹ️ Вы уже участник этого чата")
                            try:
                                check_result = await self.client(CheckChatInviteRequest(hash=invite_hash))
                                if hasattr(check_result, 'chat'):
                                    entity = check_result.chat
                                    logger.info(f"✓ Получен entity через CheckChatInviteRequest")
                            except Exception as check_error:
                                logger.warning(f"⚠️ Не удалось получить entity через CheckChatInviteRequest: {check_error}")
                                # Пробуем получить через get_entity с полной ссылкой
                                try:
                                    entity = await self.client.get_entity(link)
                                    logger.info(f"✓ Получен entity через get_entity")
                                except Exception as e:
                                    logger.error(f"❌ Не удалось получить entity: {e}")

                        # Проверяем, требуется ли подтверждение администратора
                        elif 'INVITE_REQUEST_SENT' in error_msg or 'requested to join' in error_msg.lower():
                            logger.warning(f"📨 Отправлена заявка на вступление (требуется подтверждение администратора)")

                            # Сначала получаем инфо о чате через Check
                            try:
                                check_result = await self.client(CheckChatInviteRequest(hash=invite_hash))
                                chat_title = getattr(check_result, 'title', 'Неизвестный чат')
                            except:
                                chat_title = 'Неизвестный чат'

                            # Сохраняем заявку в БД с параметрами задачи
                            import json
                            task_params_json = json.dumps(task_params) if task_params else None
                            await self.db.add_join_request(
                                account_id=account_id,
                                chat_username=link,
                                chat_title=chat_title,
                                task_type=task_type,
                                database_name=database_name,
                                task_params=task_params_json
                            )
                            join_requested = True

                        elif 'CHANNELS_TOO_MUCH' in error_msg:
                            logger.error(f"❌ Достигнут лимит чатов/каналов. Выйдите из ненужных чатов.")
                        else:
                            logger.error(f"❌ Не удалось присоединиться: {error_msg}")
                else:
                    # Для обычных публичных ссылок получаем entity напрямую
                    entity = await self.client.get_entity(chat_identifier)

                    # Пробуем вступить во все чаты/группы/каналы (если еще не участник)
                    if entity:
                        from telethon.tl.functions.channels import JoinChannelRequest

                        try:
                            logger.info(f"🔗 Попытка вступления в {getattr(entity, 'title', 'чат')}...")
                            await self.client(JoinChannelRequest(entity))
                            logger.success(f"✅ Успешно вступили в {getattr(entity, 'title', 'чат')}")
                            # Небольшая задержка после вступления
                            await self.human_simulator.random_delay(1, 3)
                        except Exception as join_error:
                            error_msg = str(join_error)
                            # Если уже участник - это нормально
                            if 'USER_ALREADY_PARTICIPANT' in error_msg or 'already' in error_msg.lower():
                                logger.info(f"ℹ️ Вы уже участник этого чата")
                            # Если требуется заявка
                            elif 'INVITE_REQUEST_SENT' in error_msg or 'request' in error_msg.lower():
                                logger.warning(f"📨 Отправлена заявка на вступление (требуется одобрение)")

                                # Сохраняем заявку в БД
                                import json
                                task_params_json = json.dumps(task_params) if task_params else None
                                await self.db.add_join_request(
                                    account_id=account_id,
                                    chat_username=chat_identifier,
                                    chat_title=getattr(entity, 'title', 'Неизвестный чат'),
                                    task_type=task_type,
                                    database_name=database_name,
                                    task_params=task_params_json
                                )
                                join_requested = True
                            elif 'CHANNELS_TOO_MUCH' in error_msg:
                                logger.error(f"❌ Достигнут лимит чатов/каналов. Выйдите из ненужных чатов.")
                            else:
                                # Любая другая ошибка - просто логируем, но продолжаем работу
                                logger.info(f"ℹ️ {error_msg}")

                # Если entity получен, проверяем доступность
                if entity:
                    if hasattr(entity, 'username') and entity.username:
                        display_name = f"@{entity.username}"
                        # Если есть topic_id — сохраняем оригинальную ссылку целиком,
                        # чтобы при рассылке parse_chat_link мог извлечь тему
                        save_identifier = link if topic_id else entity.username
                    elif is_invite_link:
                        # Для приватных чатов (invite-ссылок) сохраняем САМУ ссылку
                        display_name = getattr(entity, 'title', f"ID:{entity.id}")
                        save_identifier = link  # Сохраняем полную invite-ссылку!
                    elif hasattr(entity, 'title'):
                        display_name = entity.title
                        save_identifier = str(entity.id)
                    else:
                        display_name = str(entity.id)
                        save_identifier = str(entity.id)

                    # Если есть topic_id, добавляем к имени для отображения
                    if topic_id:
                        display_name += f" (тема {topic_id})"

                    # Проверяем доступность
                    # Для каналов get_participants не работает, поэтому просто проверяем что entity получен
                    from telethon.tl.types import Channel
                    is_channel = isinstance(entity, Channel) and entity.broadcast

                    # Добавляем в доступные ТОЛЬКО если НЕ ожидается одобрение заявки
                    if not join_requested:
                        if is_channel:
                            # Для каналов - если получили entity, значит есть доступ
                            accessible_chats.append(save_identifier)
                            channel_count += 1
                            logger.info(f"✓ Канал доступен: {display_name}")
                        else:
                            # Для групп/чатов - пробуем получить участников
                            try:
                                await self.client.get_participants(entity, limit=1)
                                accessible_chats.append(save_identifier)
                                chat_count += 1
                                logger.info(f"✓ Чат доступен: {display_name}")
                            except:
                                logger.warning(f"❌ Нет доступа к {display_name}")

                # Human-like delay
                await self.human_simulator.random_delay(2, 5)

            except UsernameInvalidError:
                logger.warning(f"❌ Неверная ссылка: {link}")
            except FloodWaitError as e:
                logger.warning(f"⏳ FloodWait! Ожидание {e.seconds} секунд...")
                await self.human_simulator.wait_for_floodwait(e.seconds)
            except Exception as e:
                logger.error(f"❌ Ошибка при обработке {link}: {e}")

        if accessible_chats:
            parts = []
            if channel_count > 0:
                parts.append(f"{channel_count} каналов")
            if chat_count > 0:
                parts.append(f"{chat_count} чатов")
            logger.success(f"✓ Доступно {' и '.join(parts)}")
        else:
            logger.warning(f"⚠️ Нет доступных чатов/каналов. Проверьте статус заявок через check_join_requests()")

        return accessible_chats

    async def collect(self, **kwargs) -> List[CollectedUser]:
        """
        Generic collect method (required by base class).
        Use specific methods instead.
        """
        raise NotImplementedError("Use collect_from_usernames() or collect_from_chats()")

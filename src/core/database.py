"""Database management for storing accounts, proxies, tasks, and logs."""

import aiosqlite
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from .config import Config
from .logger import get_logger

logger = get_logger(__name__)


class Database:
    """SQLite database manager."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Config.DATABASE_PATH
        self.db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Connect to database and create tables."""
        self.db = await aiosqlite.connect(str(self.db_path))
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info(f"База данных подключена: {self.db_path}")

    async def close(self):
        """Close database connection."""
        if self.db:
            await self.db.close()
            logger.info("База данных закрыта")

    async def _create_tables(self):
        """Create all necessary tables."""
        async with self.db.cursor() as cursor:
            # Proxies table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    username TEXT,
                    password TEXT,
                    proxy_type TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(address, port)
                )
            """)

            # Accounts table (Telegram)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT NOT NULL UNIQUE,
                    proxy_id INTEGER,
                    session_name TEXT NOT NULL,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    is_active BOOLEAN DEFAULT 1,
                    messages_sent_today INTEGER DEFAULT 0,
                    last_message_date DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (proxy_id) REFERENCES proxies(id)
                )
            """)

            # Миграции для accounts
            for col in [
                "ALTER TABLE accounts ADD COLUMN username TEXT DEFAULT ''",
                "ALTER TABLE accounts ADD COLUMN first_name TEXT DEFAULT ''",
                "ALTER TABLE accounts ADD COLUMN last_name TEXT DEFAULT ''",
            ]:
                try:
                    await cursor.execute(col)
                    await self.db.commit()
                except Exception:
                    pass

            # VK Accounts table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS vk_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT NOT NULL UNIQUE,
                    access_token TEXT NOT NULL,
                    proxy_id INTEGER,
                    is_active BOOLEAN DEFAULT 1,
                    messages_sent_today INTEGER DEFAULT 0,
                    last_message_date DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (proxy_id) REFERENCES proxies(id)
                )
            """)

            # WhatsApp Accounts table (каждый аккаунт = свой браузерный профиль + прокси)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    proxy_id INTEGER NOT NULL,
                    profile_dir TEXT NOT NULL UNIQUE,
                    logged_in INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (proxy_id) REFERENCES proxies(id)
                )
            """)

            # Миграция: старая схема whatsapp_accounts (display_name/profile_dir_name)
            # → актуальная (name/profile_dir/logged_in). Пересобираем таблицу,
            # сохраняя существующие аккаунты и их браузерные профили.
            await cursor.execute("PRAGMA table_info(whatsapp_accounts)")
            _wa_cols = [row[1] for row in await cursor.fetchall()]
            if 'name' not in _wa_cols and 'display_name' in _wa_cols:
                await cursor.execute("ALTER TABLE whatsapp_accounts RENAME TO whatsapp_accounts_old")
                await cursor.execute("""
                    CREATE TABLE whatsapp_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT DEFAULT '',
                        phone TEXT DEFAULT '',
                        proxy_id INTEGER NOT NULL,
                        profile_dir TEXT NOT NULL UNIQUE,
                        logged_in INTEGER DEFAULT 0,
                        is_active BOOLEAN DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (proxy_id) REFERENCES proxies(id)
                    )
                """)
                await cursor.execute("""
                    INSERT INTO whatsapp_accounts
                        (id, name, phone, proxy_id, profile_dir, logged_in, is_active, created_at)
                    SELECT id, display_name, phone, proxy_id, profile_dir_name, 0, is_active, created_at
                    FROM whatsapp_accounts_old
                """)
                await cursor.execute("DROP TABLE whatsapp_accounts_old")
                await self.db.commit()
                logger.info("Миграция whatsapp_accounts: схема обновлена (display_name→name, profile_dir_name→profile_dir)")

            # Tasks table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT NOT NULL,
                    account_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    total_recipients INTEGER DEFAULT 0,
                    sent_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            """)

            # Messages log table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS message_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    account_id INTEGER NOT NULL,
                    recipient TEXT NOT NULL,
                    message_text TEXT,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
            """)

            # Audience collections table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS audience_collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    collection_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    user_id BIGINT,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
            """)

            # Join requests tracking
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS join_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    chat_username TEXT NOT NULL,
                    chat_title TEXT,
                    status TEXT DEFAULT 'pending',
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accepted_at TIMESTAMP,
                    task_type TEXT,
                    database_name TEXT,
                    task_params TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(id),
                    UNIQUE(account_id, chat_username)
                )
            """)

            # Миграция: добавляем новые колонки если их нет
            try:
                await cursor.execute("ALTER TABLE join_requests ADD COLUMN chat_title TEXT")
                await self.db.commit()
            except:
                pass

            try:
                await cursor.execute("ALTER TABLE join_requests ADD COLUMN task_type TEXT")
                await self.db.commit()
            except:
                pass

            try:
                await cursor.execute("ALTER TABLE join_requests ADD COLUMN database_name TEXT")
                await self.db.commit()
            except:
                pass

            try:
                await cursor.execute("ALTER TABLE join_requests ADD COLUMN task_params TEXT")
                await self.db.commit()
            except:
                pass

            # Named audience databases
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS named_audiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    collection_name TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    source_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (account_id) REFERENCES accounts(id),
                    UNIQUE(account_id, collection_name, user_id)
                )
            """)

            # Message history - track who we already messaged
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS message_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (account_id) REFERENCES accounts(id),
                    UNIQUE(account_id, user_id)
                )
            """)

            # Broadcast progress - track cycles progress for VK chat broadcasts
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS vk_broadcast_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    task_hash TEXT NOT NULL,
                    current_cycle INTEGER DEFAULT 1,
                    total_cycles INTEGER NOT NULL,
                    is_completed BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (account_id) REFERENCES vk_accounts(id),
                    UNIQUE(account_id, task_hash)
                )
            """)

            # WhatsApp sent log - журнал отправок для дневного лимита на аккаунт
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS wa_sent_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    phone TEXT,
                    seller_inn TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Миграция: добавить access_hash если колонки ещё нет
            try:
                await cursor.execute("ALTER TABLE named_audiences ADD COLUMN access_hash BIGINT")
                await self.db.commit()
            except Exception:
                pass  # Колонка уже существует

            await self.db.commit()

    # ============= PROXY METHODS =============

    async def add_proxy(self, address: str, port: int, username: Optional[str] = None,
                       password: Optional[str] = None, proxy_type: str = 'socks5') -> int:
        """Add a new proxy."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO proxies (address, port, username, password, proxy_type)
                VALUES (?, ?, ?, ?, ?)
            """, (address, port, username, password, proxy_type.lower()))
            await self.db.commit()
            return cursor.lastrowid

    async def get_proxy(self, proxy_id: int) -> Optional[Dict[str, Any]]:
        """Get proxy by ID."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_proxies(self) -> List[Dict[str, Any]]:
        """Get all active proxies."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM proxies WHERE is_active = 1")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete_proxy(self, proxy_id: int):
        """
        Delete a proxy by ID.

        Args:
            proxy_id: Proxy ID to delete
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
            await self.db.commit()
            logger.info(f"Deleted proxy with ID {proxy_id}")

    # ============= ACCOUNT METHODS =============

    async def add_account(self, phone: str, proxy_id: Optional[int] = None,
                           username: str = '', first_name: str = '', last_name: str = '') -> int:
        """Add a new account."""
        session_name = f"session_{phone.replace('+', '')}"
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO accounts (phone, proxy_id, session_name, username, first_name, last_name)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (phone, proxy_id, session_name, username, first_name, last_name))
            await self.db.commit()
            return cursor.lastrowid

    async def update_account_info(self, account_id: int, username: str = '', first_name: str = '', last_name: str = ''):
        """Update username, first_name and last_name for an account."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                UPDATE accounts SET username = ?, first_name = ?, last_name = ? WHERE id = ?
            """, (username, first_name, last_name, account_id))
            await self.db.commit()

    async def update_account_proxy(self, account_id: int, proxy_id: Optional[int]):
        """Update proxy for an account."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                UPDATE accounts SET proxy_id = ? WHERE id = ?
            """, (proxy_id, account_id))
            await self.db.commit()

    async def get_account(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Get account by ID."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_account_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Get account by phone number."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM accounts WHERE phone = ?", (phone,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_accounts(self) -> List[Dict[str, Any]]:
        """Get list of all accounts."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM accounts")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_account_messages_count(self, account_id: int):
        """Increment messages sent count for today."""
        today = datetime.now().date()
        async with self.db.cursor() as cursor:
            # Check if last message was today
            await cursor.execute("""
                SELECT messages_sent_today, last_message_date
                FROM accounts WHERE id = ?
            """, (account_id,))
            row = await cursor.fetchone()

            if row:
                last_date = row[1]
                count = row[0] if last_date == str(today) else 0

                await cursor.execute("""
                    UPDATE accounts
                    SET messages_sent_today = ?, last_message_date = ?
                    WHERE id = ?
                """, (count + 1, str(today), account_id))
                await self.db.commit()

    async def can_send_message(self, account_id: int) -> bool:
        """Check if account can send more messages today."""
        account = await self.get_account(account_id)
        if not account:
            return False

        today = datetime.now().date()
        last_date = account.get('last_message_date')

        # Reset count if it's a new day
        if last_date != str(today):
            return True

        return account.get('messages_sent_today', 0) < Config.MAX_MESSAGES_PER_DAY

    async def delete_account(self, account_id: int):
        """
        Delete an account by ID.

        Args:
            account_id: Account ID to delete
        """
        async with self.db.cursor() as cursor:
            # Delete related data first
            await cursor.execute("DELETE FROM named_audiences WHERE account_id = ?", (account_id,))
            await cursor.execute("DELETE FROM message_history WHERE account_id = ?", (account_id,))
            await cursor.execute("DELETE FROM join_requests WHERE account_id = ?", (account_id,))
            await cursor.execute("DELETE FROM message_logs WHERE account_id = ?", (account_id,))
            await cursor.execute("DELETE FROM tasks WHERE account_id = ?", (account_id,))

            # Delete account itself
            await cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            await self.db.commit()
            logger.info(f"Deleted account with ID {account_id} and all related data")

    # ============= VK ACCOUNT METHODS =============

    async def add_vk_account(self, phone: str, access_token: str, proxy_id: Optional[int] = None) -> int:
        """Add a new VK account."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO vk_accounts (phone, access_token, proxy_id)
                VALUES (?, ?, ?)
            """, (phone, access_token, proxy_id))
            await self.db.commit()
            return cursor.lastrowid

    async def get_vk_account(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Get VK account by ID."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM vk_accounts WHERE id = ?", (account_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_vk_account_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Get VK account by phone number."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM vk_accounts WHERE phone = ?", (phone,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_vk_account_token(self, account_id: int, access_token: str):
        """Update VK account access token."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                UPDATE vk_accounts SET access_token = ? WHERE id = ?
            """, (access_token, account_id))
            await self.db.commit()

    async def list_vk_accounts(self) -> List[Dict[str, Any]]:
        """Get list of all VK accounts."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM vk_accounts")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete_vk_account(self, account_id: int):
        """
        Delete a VK account by ID.

        Args:
            account_id: VK Account ID to delete
        """
        async with self.db.cursor() as cursor:
            # VK аккаунты пока не имеют связанных таблиц
            # Когда добавим коллекции/сообщения для VK - добавим удаление

            # Delete account itself
            await cursor.execute("DELETE FROM vk_accounts WHERE id = ?", (account_id,))
            await self.db.commit()
            logger.info(f"Deleted VK account with ID {account_id}")

    # ============= WHATSAPP ACCOUNT METHODS =============

    async def add_whatsapp_account(self, name: str, proxy_id: int, profile_dir: str,
                                    phone: str = '') -> int:
        """Add a new WhatsApp account."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO whatsapp_accounts (name, phone, proxy_id, profile_dir)
                VALUES (?, ?, ?, ?)
            """, (name, phone, proxy_id, profile_dir))
            await self.db.commit()
            return cursor.lastrowid

    async def get_whatsapp_account(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Get WhatsApp account by ID."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM whatsapp_accounts WHERE id = ?", (account_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_whatsapp_accounts(self) -> List[Dict[str, Any]]:
        """Get list of all WhatsApp accounts."""
        async with self.db.cursor() as cursor:
            await cursor.execute("SELECT * FROM whatsapp_accounts ORDER BY id")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_whatsapp_account_proxy(self, account_id: int, proxy_id: int):
        """Update proxy for a WhatsApp account."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                UPDATE whatsapp_accounts SET proxy_id = ? WHERE id = ?
            """, (proxy_id, account_id))
            await self.db.commit()

    async def update_whatsapp_account_info(self, account_id: int, name: Optional[str] = None,
                                            phone: Optional[str] = None):
        """Update display name and/or phone for a WhatsApp account."""
        async with self.db.cursor() as cursor:
            if name is not None:
                await cursor.execute("UPDATE whatsapp_accounts SET name = ? WHERE id = ?",
                                     (name, account_id))
            if phone is not None:
                await cursor.execute("UPDATE whatsapp_accounts SET phone = ? WHERE id = ?",
                                     (phone, account_id))
            await self.db.commit()

    async def set_whatsapp_logged_in(self, account_id: int, logged_in: bool):
        """Mark a WhatsApp account as logged in / out."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                UPDATE whatsapp_accounts SET logged_in = ? WHERE id = ?
            """, (1 if logged_in else 0, account_id))
            await self.db.commit()

    async def delete_whatsapp_account(self, account_id: int):
        """Delete a WhatsApp account by ID (вместе с журналом отправок)."""
        async with self.db.cursor() as cursor:
            await cursor.execute("DELETE FROM wa_sent_log WHERE account_id = ?", (account_id,))
            await cursor.execute("DELETE FROM whatsapp_accounts WHERE id = ?", (account_id,))
            await self.db.commit()
            logger.info(f"Deleted WhatsApp account with ID {account_id}")

    # ============= WHATSAPP SEND LOG (дневной лимит) =============

    async def wa_log_sent(self, account_id: int, phone: str = '', seller_inn: str = ''):
        """Записать факт отправки сообщения с WhatsApp-аккаунта."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO wa_sent_log (account_id, phone, seller_inn)
                VALUES (?, ?, ?)
            """, (account_id, phone, seller_inn))
            await self.db.commit()

    async def wa_sent_today(self, account_id: int) -> int:
        """Сколько сообщений отправлено с аккаунта сегодня (локальная дата)."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT COUNT(*) FROM wa_sent_log
                WHERE account_id = ? AND DATE(sent_at) = DATE('now', 'localtime')
            """, (account_id,))
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def wa_sent_today_all(self) -> Dict[int, int]:
        """Словарь {account_id: отправлено сегодня} по всем аккаунтам."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT account_id, COUNT(*) FROM wa_sent_log
                WHERE DATE(sent_at) = DATE('now', 'localtime')
                GROUP BY account_id
            """)
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}

    # ============= TASK METHODS =============

    async def create_task(self, task_name: str, account_id: int, total_recipients: int = 0) -> int:
        """Create a new task."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO tasks (task_name, account_id, total_recipients, started_at)
                VALUES (?, ?, ?, ?)
            """, (task_name, account_id, total_recipients, datetime.now()))
            await self.db.commit()
            return cursor.lastrowid

    async def update_task_status(self, task_id: int, status: str):
        """Update task status."""
        async with self.db.cursor() as cursor:
            completed_at = datetime.now() if status == 'completed' else None
            await cursor.execute("""
                UPDATE tasks SET status = ?, completed_at = ?
                WHERE id = ?
            """, (status, completed_at, task_id))
            await self.db.commit()

    async def update_task_counts(self, task_id: int, sent: bool = True):
        """Update task sent/failed counts."""
        field = 'sent_count' if sent else 'failed_count'
        async with self.db.cursor() as cursor:
            await cursor.execute(f"""
                UPDATE tasks SET {field} = {field} + 1
                WHERE id = ?
            """, (task_id,))
            await self.db.commit()

    # ============= MESSAGE LOG METHODS =============

    async def log_message(self, task_id: int, account_id: int, recipient: str,
                         message_text: str, status: str, error_message: Optional[str] = None):
        """Log a message send attempt."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO message_logs (task_id, account_id, recipient, message_text, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (task_id, account_id, recipient, message_text, status, error_message))
            await self.db.commit()

    # ============= AUDIENCE COLLECTION METHODS =============

    async def save_collected_user(self, task_id: int, collection_type: str, source: str,
                                  user_id: int, username: Optional[str] = None,
                                  first_name: Optional[str] = None, last_name: Optional[str] = None):
        """Save collected user to database."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO audience_collections
                (task_id, collection_type, source, user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (task_id, collection_type, source, user_id, username, first_name, last_name))
            await self.db.commit()

    async def get_collected_users(self, task_id: int) -> List[Dict[str, Any]]:
        """Get all collected users for a task."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM audience_collections WHERE task_id = ?
            """, (task_id,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ============= JOIN REQUEST METHODS =============

    async def add_join_request(
        self,
        account_id: int,
        chat_username: str,
        chat_title: str = None,
        task_type: str = None,
        database_name: str = None,
        task_params: str = None
    ):
        """
        Track a join request with task parameters.

        Args:
            account_id: Account ID
            chat_username: Chat link/username
            chat_title: Chat title
            task_type: Type of task ('collect_users', 'collect_chats', 'save_chats')
            database_name: Database name to save results to
            task_params: JSON string with task parameters
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO join_requests
                (account_id, chat_username, chat_title, status, task_type, database_name, task_params)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """, (account_id, chat_username, chat_title, task_type, database_name, task_params))
            await self.db.commit()

    async def update_join_request(self, account_id: int, chat_username: str, status: str):
        """Update join request status."""
        async with self.db.cursor() as cursor:
            accepted_at = datetime.now() if status == 'accepted' else None
            await cursor.execute("""
                UPDATE join_requests
                SET status = ?, accepted_at = ?
                WHERE account_id = ? AND chat_username = ?
            """, (status, accepted_at, account_id, chat_username))
            await self.db.commit()

    async def get_pending_join_requests(self, account_id: int) -> List[dict]:
        """Get all pending join requests for an account with full details."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT chat_username, chat_title, task_type, database_name, task_params, requested_at
                FROM join_requests
                WHERE account_id = ? AND status = 'pending'
            """, (account_id,))
            rows = await cursor.fetchall()
            return [{
                'username': row[0],
                'title': row[1],
                'task_type': row[2],
                'database_name': row[3],
                'task_params': row[4],
                'requested_at': row[5]
            } for row in rows]

    async def get_all_pending_join_requests(self) -> List[dict]:
        """Get all pending join requests for ALL accounts with account info."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT
                    jr.account_id,
                    jr.chat_username,
                    jr.chat_title,
                    jr.task_type,
                    jr.database_name,
                    jr.task_params,
                    jr.requested_at,
                    a.phone
                FROM join_requests jr
                JOIN accounts a ON jr.account_id = a.id
                WHERE jr.status = 'pending'
                ORDER BY jr.account_id, jr.requested_at
            """)
            rows = await cursor.fetchall()
            return [{
                'account_id': row[0],
                'username': row[1],
                'title': row[2],
                'task_type': row[3],
                'database_name': row[4],
                'task_params': row[5],
                'requested_at': row[6],
                'account_phone': row[7]
            } for row in rows]

    async def get_accepted_join_requests(self, account_id: int) -> List[dict]:
        """Get all accepted join requests for an account with full details."""
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT chat_username, chat_title, task_type, database_name, task_params, accepted_at
                FROM join_requests
                WHERE account_id = ? AND status = 'accepted'
            """, (account_id,))
            rows = await cursor.fetchall()
            return [{
                'username': row[0],
                'title': row[1],
                'task_type': row[2],
                'database_name': row[3],
                'task_params': row[4],
                'accepted_at': row[5]
            } for row in rows]

    # ============= NAMED AUDIENCE DATABASE METHODS =============

    async def save_audience_collection(self, account_id: int, collection_name: str, users: List[Any]):
        """
        Save collected users to a named database.
        Глобальный антидубликат: если user_id уже есть в ЛЮБОЙ базе ЛЮБОГО аккаунта — пропускаем.
        (Не применяется к базам чатов.)

        Args:
            account_id: Account ID
            collection_name: Name of the collection (e.g., "my_friends")
            users: List of CollectedUser objects
        """
        is_chat_database = users and getattr(users[0], 'last_name', None) == '[CHAT_FOR_BROADCAST]'

        async with self.db.cursor() as cursor:
            saved_count = 0
            skipped_count = 0

            for user in users:
                try:
                    # Глобальный антидубликат для баз пользователей
                    if not is_chat_database:
                        await cursor.execute("""
                            SELECT 1 FROM named_audiences
                            WHERE user_id = ? AND last_name != '[CHAT_FOR_BROADCAST]'
                            LIMIT 1
                        """, (user.user_id,))
                        if await cursor.fetchone():
                            skipped_count += 1
                            continue

                    await cursor.execute("""
                        INSERT OR REPLACE INTO named_audiences
                        (account_id, collection_name, user_id, username, first_name, last_name, source_name, access_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        account_id,
                        collection_name,
                        user.user_id,
                        user.username,
                        user.first_name,
                        user.last_name,
                        getattr(user, 'source_name', None),
                        getattr(user, 'access_hash', None)
                    ))
                    saved_count += 1
                except Exception as e:
                    logger.warning(f"Failed to save user {user.user_id} to database: {e}")

            await self.db.commit()

            if skipped_count > 0:
                logger.info(f"Сохранено {saved_count} в '{collection_name}' (пропущено дубликатов: {skipped_count})")
            else:
                logger.info(f"Сохранено {saved_count} пользователей в '{collection_name}'")

    async def load_audience_collection(self, collection_name: str, account_id: int = None) -> List[Any]:
        """
        Load users from a named database.
        Ищет по имени базы во всех аккаунтах.

        Args:
            collection_name: Name of the collection
            account_id: Не используется (оставлен для обратной совместимости)

        Returns:
            List of CollectedUser objects
        """
        from ..telegram.collectors.base import CollectedUser

        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT user_id, username, first_name, last_name, source_name, access_hash
                FROM named_audiences
                WHERE collection_name = ?
            """, (collection_name,))
            rows = await cursor.fetchall()

            users = []
            for row in rows:
                users.append(CollectedUser(
                    user_id=row[0],
                    username=row[1],
                    first_name=row[2],
                    last_name=row[3],
                    source_name=row[4],
                    access_hash=row[5]
                ))

            return users

    async def get_all_collected_users(self) -> List[Dict[str, Any]]:
        """
        Get ALL collected users from ALL databases (for anti-duplicate checks).

        Returns:
            List of dicts with user_id, username, etc.
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT DISTINCT user_id, username, first_name, last_name, source_name
                FROM named_audiences
            """)
            rows = await cursor.fetchall()

            users = []
            for row in rows:
                users.append({
                    'user_id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'last_name': row[3],
                    'source_name': row[4]
                })

            return users

    async def list_audience_collections(self, account_id: int) -> List[Dict[str, Any]]:
        """
        List all named audience collections for an account.

        Args:
            account_id: Account ID

        Returns:
            List of dictionaries with collection info
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT
                    collection_name,
                    COUNT(*) as count,
                    MIN(created_at) as created_at,
                    MAX(last_name) as sample_last_name
                FROM named_audiences
                WHERE account_id = ?
                GROUP BY collection_name
                ORDER BY created_at DESC
            """, (account_id,))
            rows = await cursor.fetchall()

            return [
                {
                    'name': row[0],
                    'count': row[1],
                    'created_at': row[2],
                    'is_chat_database': row[3] == '[CHAT_FOR_BROADCAST]'  # Определяем тип базы
                }
                for row in rows
            ]

    async def delete_audience_collection(self, account_id: int, collection_name: str):
        """
        Delete a named audience collection and clear anti-duplicate marks for users ONLY from this database.

        ВАЖНО: Антидубликаты снимаются только для пользователей, которые НЕ находятся в других активных базах!

        Args:
            account_id: Account ID
            collection_name: Name of the collection to delete
        """
        async with self.db.cursor() as cursor:
            # Сначала проверяем, является ли это базой пользователей
            await cursor.execute("""
                SELECT last_name FROM named_audiences
                WHERE account_id = ? AND collection_name = ?
                LIMIT 1
            """, (account_id, collection_name))
            row = await cursor.fetchone()

            is_chat_database = row and row[0] == '[CHAT_FOR_BROADCAST]'

            # Если это база пользователей, снимаем антидубликаты
            if not is_chat_database:
                # Получаем список user_id из удаляемой базы
                await cursor.execute("""
                    SELECT DISTINCT user_id FROM named_audiences
                    WHERE account_id = ? AND collection_name = ?
                """, (account_id, collection_name))
                user_ids_to_check = [row[0] for row in await cursor.fetchall()]

                # Для каждого пользователя проверяем, есть ли он в других базах (глобально)
                for user_id in user_ids_to_check:
                    await cursor.execute("""
                        SELECT COUNT(*) FROM named_audiences
                        WHERE user_id = ? AND NOT (account_id = ? AND collection_name = ?) AND last_name != '[CHAT_FOR_BROADCAST]'
                    """, (user_id, account_id, collection_name))
                    count = (await cursor.fetchone())[0]

                    # Если пользователь НЕ находится в других базах, снимаем антидубликат
                    if count == 0:
                        await cursor.execute("""
                            DELETE FROM message_history
                            WHERE user_id = ?
                        """, (user_id,))

                logger.info(f"Сняты антидубликаты для {len(user_ids_to_check)} пользователей (которые не в других базах)")

            # Удаляем саму базу
            await cursor.execute("""
                DELETE FROM named_audiences
                WHERE account_id = ? AND collection_name = ?
            """, (account_id, collection_name))
            await self.db.commit()
            logger.info(f"Deleted collection '{collection_name}'")

    # ============= MESSAGE HISTORY METHODS =============

    async def mark_user_messaged(self, account_id: int, user_id: int):
        """
        Mark a user as messaged to prevent duplicate sends.

        Args:
            account_id: Account ID
            user_id: User ID who received the message
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT OR IGNORE INTO message_history (account_id, user_id)
                VALUES (?, ?)
            """, (account_id, user_id))
            await self.db.commit()

    async def was_user_messaged(self, account_id: int, user_id: int) -> bool:
        """
        Check if a user was already messaged by ANY account.

        Args:
            account_id: Не используется (оставлен для совместимости)
            user_id: User ID to check

        Returns:
            True if user was already messaged, False otherwise
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT 1 FROM message_history
                WHERE user_id = ?
                LIMIT 1
            """, (user_id,))
            row = await cursor.fetchone()
            return row is not None

    async def filter_already_messaged(self, account_id: int, users: List[Any]) -> List[Any]:
        """
        Filter out users who were already messaged by ANY account.

        Args:
            account_id: Не используется (оставлен для совместимости)
            users: List of CollectedUser objects

        Returns:
            List of users who haven't been messaged yet
        """
        filtered_users = []
        for user in users:
            is_messaged = await self.was_user_messaged(account_id, user.user_id)
            if not is_messaged:
                filtered_users.append(user)
            else:
                logger.info(f"Пропускаем пользователя {user.username or user.user_id} - уже писали ранее")

        return filtered_users

    # ============= VK BROADCAST PROGRESS METHODS =============

    async def get_vk_broadcast_progress(self, account_id: int, task_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get broadcast progress for a task.

        Args:
            account_id: VK Account ID
            task_hash: Task hash (unique identifier)

        Returns:
            Progress dict or None if not found
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                SELECT current_cycle, total_cycles, is_completed
                FROM vk_broadcast_progress
                WHERE account_id = ? AND task_hash = ?
            """, (account_id, task_hash))
            row = await cursor.fetchone()

            if row:
                return {
                    'current_cycle': row[0],
                    'total_cycles': row[1],
                    'is_completed': bool(row[2])
                }
            return None

    async def save_vk_broadcast_progress(
        self,
        account_id: int,
        task_hash: str,
        current_cycle: int,
        total_cycles: int,
        is_completed: bool = False
    ):
        """
        Save or update broadcast progress.

        Args:
            account_id: VK Account ID
            task_hash: Task hash (unique identifier)
            current_cycle: Current cycle number
            total_cycles: Total number of cycles
            is_completed: Whether task is completed
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                INSERT OR REPLACE INTO vk_broadcast_progress
                (account_id, task_hash, current_cycle, total_cycles, is_completed, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (account_id, task_hash, current_cycle, total_cycles, is_completed, datetime.now()))
            await self.db.commit()

    async def delete_vk_broadcast_progress(self, account_id: int, task_hash: str):
        """
        Delete broadcast progress (when task is fully completed).

        Args:
            account_id: VK Account ID
            task_hash: Task hash
        """
        async with self.db.cursor() as cursor:
            await cursor.execute("""
                DELETE FROM vk_broadcast_progress
                WHERE account_id = ? AND task_hash = ?
            """, (account_id, task_hash))
            await self.db.commit()

"""
ПРОСТОЙ API ДЛЯ АВТОМАТИЗАЦИИ WILDBERRIES

Импортируй этот файл в свою задачу и используй готовые функции.
Вся сложная логика спрятана внутри - ты просто передаешь параметры.

ВАЖНО: Прокси обязателен для безопасной работы!
"""

import sys
import os
from typing import Optional, Dict, List, Union  # Union нужен для seller_count_sales

# Настройка UTF-8 для Windows консоли (для emoji)
if sys.platform == 'win32':
    os.system('chcp 65001 >nul')
    if sys.stdout.encoding != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from src.core.logger import get_logger
from src.wb.client import WBClientManager
from src.wb.collector import WBCollector

logger = get_logger(__name__)


# ============================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================

_client: Optional[WBClientManager] = None


# ============================================
# ФУНКЦИЯ 1: ПОДКЛЮЧЕНИЕ БРАУЗЕРА
# ============================================

async def connect(
    profile: str,
    proxy_address: Optional[str] = None,
    proxy_port: Optional[int] = None,
    proxy_username: Optional[str] = None,
    proxy_password: Optional[str] = None,
    proxy_type: str = "http"
) -> Dict:
    """
    Подключить браузер Chrome с профилем (прокси опционально).

    При ПЕРВОМ запуске:
    - Копирует профиль в отдельную папку (data/wb/chrome_profile/)
    - Открывает браузер и ждет авторизации в mpstats
    - После авторизации профиль сохраняется для будущих запусков

    При ПОСЛЕДУЮЩИХ запусках:
    - Использует сохраненный профиль с авторизованным mpstats

    Параметры:
        profile: Путь к профилю Chrome с установленным расширением mpstats
        proxy_address: Адрес прокси (опционально)
        proxy_port: Порт прокси (опционально)
        proxy_username: Логин прокси (опционально)
        proxy_password: Пароль прокси (опционально)
        proxy_type: Тип подключения ("http" / "socks5")

    Примеры:
        # С прокси
        await connect(
            profile=r"C:\\Users\\1\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 5",
            proxy_address="0.0.0.0",
            proxy_port=0,
            proxy_username="proxy_user",
            proxy_password="proxy_pass",
            proxy_type="http"
        )

        # Без прокси
        await connect(
            profile=r"C:\\Users\\1\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 5"
        )
    """
    global _client

    # Валидация обязательных параметров
    if not profile:
        raise ValueError("Параметр profile обязателен")

    # Проверка пути профиля (только при первом запуске)
    from src.wb.client import WB_PROFILE_DIR
    from pathlib import Path
    profile_name = Path(profile).name
    saved_profile = WB_PROFILE_DIR / profile_name

    if not saved_profile.exists() and not os.path.exists(profile):
        raise ValueError(f"Путь profile не существует: {profile}")

    print(f"\n{'='*60}")
    print(f"ПОДКЛЮЧЕНИЕ WILDBERRIES")
    print(f"{'='*60}\n")

    print(f"Профиль: {profile_name}")
    if proxy_address:
        print(f"Прокси: {proxy_address}:{proxy_port} ({proxy_type})")
        logger.info(f"Подключение к WB с прокси {proxy_address}:{proxy_port}")
    else:
        print(f"Прокси: НЕ используется")
        logger.info("Подключение к WB БЕЗ прокси")

    # Создание клиента
    _client = WBClientManager(
        profile=profile,
        proxy_address=proxy_address,
        proxy_port=proxy_port,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
        proxy_type=proxy_type
    )

    # Подключение
    try:
        _client.connect()
        print(f"\nChrome запущен успешно")
        print(f"{'='*60}\n")

        logger.info("WB: браузер успешно запущен")

        return {
            'status': 'connected',
            'profile': profile_name,
            'proxy': f"{proxy_address}:{proxy_port}" if proxy_address else None,
            'first_run': _client._is_first_run
        }

    except Exception as e:
        print(f"\nОшибка подключения: {e}")
        logger.error(f"WB: ошибка подключения - {e}")
        _client = None
        raise


# ============================================
# ФУНКЦИЯ 2: ОТКЛЮЧЕНИЕ
# ============================================

async def disconnect():
    """
    Корректно завершить работу и закрыть браузер.
    Профиль сохраняется для будущих запусков.

    Пример:
        await disconnect()
    """
    global _client

    if _client:
        try:
            _client.disconnect()
            print(f"\nWB: браузер закрыт")
            logger.info("WB: браузер закрыт")
        except Exception as e:
            print(f"\nОшибка при закрытии браузера: {e}")
            logger.warning(f"WB: ошибка при закрытии - {e}")
        finally:
            _client = None
    else:
        print(f"\nБраузер не был запущен")


# ============================================
# ФУНКЦИЯ 3: СБОР ПРОДАВЦОВ
# ============================================

async def collect_sellers(
    search_queries: List[str],
    kolvo_for_query: int = 20,
    stroke_position: int = 1,
    # seller_period: Optional[int] = None,        # раскомментировать для включения
    # seller_count_sales: Optional[Union[int, Tuple[int, int]]] = None,
    # seller_count_reviews: Optional[int] = None,
    proverka: Optional[int] = None,
    task_id: str = 'cli'
) -> Dict:
    """
    Собрать данные о продавцах (только ИП) с Wildberries.

    Сохраняет в data/wb/sellers.json. Прогресс сохраняется автоматически.
    Карточки с видео на первом слайде пропускаются. Задержка между карточками 1-3 сек.

    Параметры:
        search_queries: Список поисковых запросов
        kolvo_for_query: Кол-во продавцов на 1 запрос (по умолчанию 20)
        stroke_position: С какой строки выдачи начинать (1 = первая)
        proverka: Макс. карточек для проверки на 1 запрос (None = без лимита)
        task_id: ID задачи (для файла прогресса)

    Пример:
        await collect_sellers(
            search_queries=["сумка женская", "кроссовки"],
            kolvo_for_query=5,
            stroke_position=1,
            proverka=400
        )
    """
    global _client

    if not _client:
        raise RuntimeError("Сначала вызовите connect()")

    if not search_queries:
        raise ValueError("search_queries не может быть пустым")

    logger.info(f"Запуск сбора: {len(search_queries)} запросов")

    # Строим конфигурации запросов (все с одинаковыми настройками)
    query_configs = [
        {
            'query': q,
            'kolvo_for_query': kolvo_for_query,
            'stroke_position': stroke_position,
            'proverka': proverka,
        }
        for q in search_queries
    ]

    # Создаём сборщик
    collector = WBCollector(_client.driver, task_id=task_id)

    # Запускаем сбор
    result = collector.collect(query_configs=query_configs)

    logger.info(f"Сбор завершен: {result['total_saved']} продавцов")

    return result

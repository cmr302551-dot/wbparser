"""
Wildberries Seller Collector (async)

Сбор данных о продавцах (ИП) с Wildberries.
Каждая задача работает в своей вкладке (Page) общего браузерного контекста,
поэтому несколько задач можно запускать параллельно в одном профиле.
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Union
from pathlib import Path

from playwright.async_api import Page, Locator, TimeoutError as PlaywrightTimeoutError

from src.core.logger import get_logger
from src.core.config import Config

logger = get_logger(__name__)

# Файлы данных
PROGRESS_FILE = Config.DATA_DIR / 'wb' / 'collector_progress.json'
SELLERS_FILE = Config.DATA_DIR / 'wb' / 'sellers.json'

# Общая блокировка на запись sellers.json — несколько задач могут сохранять
# продавцов одновременно (один event loop, поэтому обычного asyncio.Lock достаточно)
_sellers_file_lock = asyncio.Lock()


def _titlecase_name(s: str) -> str:
    """Привести имя/фамилию/отчество к нормальному виду: каждое слово с заглавной,
    остальные строчные. «ИВАНОВ» → «Иванов», «иванова-петрова» → «Иванова-Петрова»."""
    if not s:
        return s
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), s)


class WBCollector:
    """Сборщик данных о продавцах с Wildberries."""

    # =====================================================
    # XPath локаторы
    # =====================================================

    # Поисковая строка
    XPATH_SEARCH_INPUT = '//*[@id="searchInput"]'

    # Карточка товара в выдаче (index меняется: 0, 1, 2...)
    XPATH_CARD_LINK = "//article[@data-card-index='{index}']//a[@class='product-card__link j-card-link j-open-full-product-card']"

    # Константы
    CARDS_PER_ROW = 5

    # Название продавца на странице карточки (для антидубликата)
    XPATH_SELLER_NAME_ON_CARD = "//span[contains(@class, 'sellerInfoNameDefaultText')]"

    # Ссылка на страницу продавца (для клика)
    XPATH_SELLER_LINK = "//span[contains(@class, 'sellerInfoNameDefaultText')]/ancestor::a[1]"

    # Первый слайд миниатюр (для проверки на видео)
    XPATH_FIRST_SLIDE_VIDEO = "//div[contains(@class, 'miniaturesWrapper')]//div[contains(@class, 'miniatureSlide')][1]//*[@data-vjs-player]"

    # Страница продавца
    XPATH_SELLER_PERIOD = "//p[contains(text(), 'Wildberries')]/preceding-sibling::p"
    XPATH_SELLER_SALES = "//p[contains(text(), 'продан')]/preceding-sibling::p"
    XPATH_SELLER_REVIEWS = "//*[@id='reactContainers']/div[1]/div/div[2]/div/div/div[1]/div[2]/div[2]/span[2]"
    XPATH_SELLER_TITLE = "//*[@id='reactContainers']/div[1]/div/div[2]/div/div/div[1]/div[2]/div[1]/h2"

    # Кнопка "i" с информацией о продавце
    XPATH_SELLER_INFO_BTN = "//*[@id='reactContainers']/div[1]/div/div[2]/div/div/div[1]/div[2]/div[1]/span"

    # Информация о продавце (после клика на "i")
    XPATH_SELLER_INFO_LINE1 = "//div[contains(@class, 'tooltip-supplier')]//div[@class='tooltip__content']/p[1]"
    XPATH_SELLER_INN = "//div[contains(@class, 'tooltip-supplier')]//p[contains(., 'ИНН:')]"
    XPATH_SELLER_OGRNIP = "//div[contains(@class, 'tooltip-supplier')]//p[contains(., 'ОГРНИП:')]"
    XPATH_SELLER_OGRN = "//div[contains(@class, 'tooltip-supplier')]//p[contains(., 'ОГРН:') and not(contains(., 'ОГРНИП:'))]"

    def __init__(self, page: Page, task_id: str = 'default', log_callback=None):
        """
        Args:
            page: Playwright Page — собственная вкладка задачи (поисковая выдача)
            task_id:  ID задачи
            log_callback: Функция для отправки логов в WebSocket (msg, task_id)
        """
        self.context = page.context
        self.task_id = task_id
        self.log_callback = log_callback
        self.stop_requested = False

        # Главная страница задачи (поисковая выдача) — своя вкладка
        self.main_page: Page = page
        # Вкладка карточки/продавца (открывается по требованию)
        self.card_page: Optional[Page] = None

        self.progress_file = Config.DATA_DIR / 'wb' / f'progress_{task_id}.json'

        # Загружаем сохраненных продавцов для антидубликатов
        self.saved_sellers = self._load_sellers()
        self.saved_shops = {s['shop'] for s in self.saved_sellers}

        # Временная база непрошедших проверку (очищается при новом запросе)
        self.skipped_shops = set()

        # Загружаем прогресс
        self.progress = self._load_progress()

        # Статистика текущего запуска
        self.stats = {
            'checked': 0,
            'skipped_duplicate': 0,
            'skipped_card_filter': 0,
            'skipped_seller_filter': 0,
            'skipped_not_ip': 0,
            'saved': 0,
            'errors': 0
        }

    @property
    def page(self) -> Page:
        """Активная страница: вкладка карточки если открыта, иначе главная."""
        return self.card_page if self.card_page else self.main_page

    # =====================================================
    # Загрузка/сохранение прогресса
    # =====================================================

    def _load_progress(self) -> Dict:
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Ошибка загрузки прогресса: {e}")
        return {
            'current_query_index': 0,
            'current_query_saved': 0,
            'current_card_index': 0
        }

    def _save_progress(self):
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    def _reset_progress(self):
        self.progress = {
            'current_query_index': 0,
            'current_query_saved': 0,
            'current_card_index': 0
        }
        self._save_progress()
        logger.info("Прогресс сброшен")

    # =====================================================
    # Загрузка/сохранение продавцов
    # =====================================================

    def _load_sellers(self) -> List[Dict]:
        if SELLERS_FILE.exists():
            try:
                with open(SELLERS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Ошибка загрузки продавцов: {e}")
        return []

    async def _save_seller(self, seller: Dict):
        self.saved_sellers.append(seller)
        self.saved_shops.add(seller['shop'])

        async with _sellers_file_lock:
            SELLERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            current = []
            if SELLERS_FILE.exists():
                try:
                    with open(SELLERS_FILE, 'r', encoding='utf-8') as f:
                        current = json.load(f)
                except Exception:
                    pass
            existing_inns = {s.get('inn') for s in current}
            if seller.get('inn') not in existing_inns:
                current.append(seller)
            with open(SELLERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(current, f, ensure_ascii=False, indent=2)

        logger.info(f"Сохранен продавец: {seller['shop']} (ИНН: {seller['inn']})")

    # =====================================================
    # Вспомогательные методы
    # =====================================================

    def _human_delay(self, min_sec: float, max_sec: float):
        """Проверка флага остановки (задержки убраны)."""
        if self.stop_requested:
            return

    def _log(self, msg: str):
        print(msg)
        logger.info(msg)
        if self.log_callback:
            self.log_callback(msg, self.task_id)

    async def _wait_page_load(self, timeout: int = 30):
        """Ждать загрузки страницы."""
        try:
            await self.page.wait_for_load_state('load', timeout=timeout * 1000)
        except PlaywrightTimeoutError:
            logger.warning("Таймаут ожидания загрузки страницы")

    async def _wait_for_element(self, xpath: str, timeout: int = 10) -> Optional[Locator]:
        """Ждать появления элемента. Возвращает Locator или None."""
        try:
            locator = self.page.locator(f"xpath={xpath}").first
            await locator.wait_for(state='visible', timeout=timeout * 1000)
            return locator
        except Exception:
            return None

    async def _get_element_text(self, xpath: str, timeout: int = 5) -> Optional[str]:
        """Получить текст элемента."""
        locator = await self._wait_for_element(xpath, timeout)
        if locator is None:
            return None
        try:
            text = await locator.inner_text(timeout=2000)
            return text.strip()
        except Exception:
            return None

    async def _scroll_to_element(self, locator: Locator):
        """Скролл к элементу."""
        try:
            await locator.scroll_into_view_if_needed()
        except Exception:
            pass

    async def _click_element(self, locator: Locator):
        """Кликнуть на элемент. JS-клик как fallback."""
        await self._scroll_to_element(locator)
        try:
            await locator.click()
        except Exception:
            try:
                await locator.evaluate("el => el.click()")
            except Exception as e:
                logger.warning(f"Ошибка клика: {e}")

    async def _dismiss_overlays(self):
        """Закрыть модальные оверлеи WB если они есть."""
        overlay_xpath = "//div[contains(@class, 'mo-modal__overlay')]"
        overlay = await self._wait_for_element(overlay_xpath, timeout=2)
        if overlay:
            logger.info("Найден оверлей, закрываем...")
            await self.page.keyboard.press("Escape")
            overlay = await self._wait_for_element(overlay_xpath, timeout=1)
            if overlay:
                try:
                    await overlay.evaluate("el => el.click()")
                except Exception:
                    pass

    # =====================================================
    # Парсинг значений
    # =====================================================

    def _parse_number(self, text: str) -> Optional[int]:
        if not text:
            return None
        digits = re.sub(r'[^\d]', '', text)
        if digits:
            return int(digits)
        return None

    def _parse_months_on_wb(self, text: str) -> int:
        if not text:
            return 0
        text = text.lower()
        if 'новый' in text:
            return 0
        total_months = 0
        years_match = re.search(r'(\d+)\s*(?:год|года|лет)', text)
        if years_match:
            total_months += int(years_match.group(1)) * 12
        months_match = re.search(r'(\d+)\s*мес', text)
        if months_match:
            total_months += int(months_match.group(1))
        return total_months

    def _calculate_months_since(self, date_str: str) -> int:
        if not date_str:
            return 0
        try:
            date_obj = datetime.strptime(date_str.strip(), "%d.%m.%Y")
            now = datetime.now()
            months = (now.year - date_obj.year) * 12 + (now.month - date_obj.month)
            if now.day < date_obj.day:
                months -= 1
            return max(0, months)
        except Exception as e:
            logger.warning(f"Ошибка парсинга даты '{date_str}': {e}")
            return 0

    def _extract_scu_from_url(self) -> Optional[str]:
        """Извлечь артикул (SCU) из URL карточки."""
        try:
            url = self.page.url
            match = re.search(r'/catalog/(\d+)/', url)
            if match:
                return match.group(1)
        except Exception as e:
            logger.warning(f"Ошибка извлечения артикула из URL: {e}")
        return None

    # =====================================================
    # Проверка антидубликата
    # =====================================================

    def check_duplicate(self, shop_name: str) -> bool:
        return shop_name in self.saved_shops or shop_name in self.skipped_shops

    def add_to_skipped(self, shop_name: str):
        if shop_name:
            self.skipped_shops.add(shop_name)
            logger.info(f"Добавлен в skipped: {shop_name}")

    def clear_skipped(self):
        count = len(self.skipped_shops)
        self.skipped_shops.clear()
        if count > 0:
            logger.info(f"Очищена временная база: {count} продавцов")

    def check_ogrnip_in_ozon(self, ogrnip: str) -> bool:
        if not ogrnip:
            return False
        ozon_sellers_file = Config.DATA_DIR / 'ozon' / 'sellers.json'
        if ozon_sellers_file.exists():
            try:
                with open(ozon_sellers_file, 'r', encoding='utf-8') as f:
                    ozon_sellers = json.load(f)
                    for seller in ozon_sellers:
                        if seller.get('ogrnip') == ogrnip:
                            logger.info(f"ОГРНИП {ogrnip} уже есть в Ozon базе - пропускаем")
                            return True
            except Exception as e:
                logger.warning(f"Ошибка проверки Ozon базы: {e}")
        return False

    # =====================================================
    # Навигация
    # =====================================================

    async def open_wildberries(self):
        """Открыть wildberries.ru."""
        logger.info("Открытие wildberries.ru...")
        print("Открытие wildberries.ru...")
        await self.main_page.goto("https://www.wildberries.ru/")
        await self._wait_page_load()

    async def search_query(self, query: str):
        """Ввести поисковый запрос и дождаться результатов."""
        logger.info(f"Поиск: {query}")
        print(f"Поиск: {query}")

        await self._dismiss_overlays()

        search_input = await self._wait_for_element(self.XPATH_SEARCH_INPUT, timeout=15)
        if not search_input:
            raise RuntimeError("Не найдена поисковая строка")

        await self._click_element(search_input)

        # Очищаем поле и вводим запрос
        await search_input.fill(query)

        await search_input.press("Enter")

        # Ждём загрузки результатов — Playwright ждёт автоматически
        await self._wait_page_load()
        await self._wait_for_element("//article[@data-card-index]", timeout=30)

    async def scroll_to_row(self, target_row: int):
        """Построчный скролл для прогрузки карточек."""
        if target_row <= 1:
            return

        pixels_per_row = 550
        rows_to_scroll = target_row - 1

        logger.info(f"Построчный скролл до строки {target_row} ({rows_to_scroll} шагов)")

        for row in range(1, rows_to_scroll + 1):
            await self.main_page.evaluate(f"window.scrollBy(0, {pixels_per_row})")
            logger.info(f"  Скролл {row}/{rows_to_scroll}: +{pixels_per_row}px")

        logger.info(f"Скролл завершён до строки {target_row}")

    async def scroll_to_card(self, card_index: int):
        """Скролл к конкретной карточке по индексу."""
        xpath = self.XPATH_CARD_LINK.format(index=card_index)
        card = await self._wait_for_element(xpath, timeout=15)
        if card:
            await self._scroll_to_element(card)
            self._human_delay(1, 1.5)
            logger.info(f"Проскроллено к карточке {card_index}")

    async def click_card(self, card_index: int) -> bool:
        """
        Открыть карточку в новой вкладке (Ctrl+Click).
        Переключает self.card_page на новую вкладку.
        """
        await self.scroll_to_card(card_index)

        xpath = self.XPATH_CARD_LINK.format(index=card_index)
        card = await self._wait_for_element(xpath, timeout=10)
        if not card:
            logger.warning(f"Карточка {card_index} не найдена")
            return False

        logger.info(f"Открытие карточки {card_index} в новой вкладке")

        try:
            async with self.context.expect_page(timeout=10000) as new_page_info:
                await card.click(modifiers=["Control"])
            self.card_page = await new_page_info.value
        except PlaywrightTimeoutError:
            logger.warning(f"Новая вкладка не открылась для карточки {card_index}")
            return False

        # Ждём загрузки страницы карточки
        try:
            await self.card_page.wait_for_load_state('load', timeout=30000)
        except PlaywrightTimeoutError:
            logger.warning("Таймаут загрузки карточки")

        await self._wait_for_card_elements()
        logger.info("Страница карточки полностью загружена")
        return True

    async def go_back(self, times: int = 1):
        """Вернуться назад в главной вкладке."""
        for i in range(times):
            logger.info(f"Назад ({i+1}/{times})")
            await self.main_page.go_back()
            await self._wait_page_load()
            if i == times - 1:
                await self._wait_for_element("//article[@data-card-index]", timeout=15)
            self._human_delay(1.5, 2.5)

    # =====================================================
    # Фильтрация карточки
    # =====================================================

    async def _wait_for_card_elements(self):
        """Дождаться ключевых элементов страницы карточки."""
        await self._wait_for_element("//*[@id='reactContainers']", timeout=15)
        seller_name = await self._wait_for_element(self.XPATH_SELLER_NAME_ON_CARD, timeout=15)
        if not seller_name:
            logger.warning("Название продавца не загрузилось на странице карточки")

    async def get_seller_name_on_card(self) -> Optional[str]:
        """Название продавца на карточке (для антидубликата)."""
        return await self._get_element_text(self.XPATH_SELLER_NAME_ON_CARD, timeout=3)

    async def has_video_as_first_slide(self) -> bool:
        """Проверить, является ли первый слайд миниатюр видео."""
        video = await self._wait_for_element(self.XPATH_FIRST_SLIDE_VIDEO, timeout=3)
        return video is not None

    # =====================================================
    # Фильтрация продавца
    # =====================================================

    async def click_seller_link(self):
        """
        Перейти на страницу продавца в той же вкладке (карточка → продавец).
        """
        seller_element = await self._wait_for_element(self.XPATH_SELLER_LINK, timeout=5)
        if not seller_element:
            self._log("  ⚠ ссылка продавца не найдена")
            return False

        logger.info("Переход на страницу продавца...")
        await self._click_element(seller_element)

        await self._wait_page_load()

        info_btn = await self._wait_for_element(self.XPATH_SELLER_INFO_BTN, timeout=10)
        if not info_btn:
            self._log("  ⚠ кнопка 'i' не найдена, пропуск")
            return False

        logger.info("Страница продавца загружена")
        return True

    async def close_card_tab(self):
        """Закрыть вкладку карточки/продавца, вернуться в выдачу."""
        if self.card_page:
            try:
                await self.card_page.close()
            except Exception:
                pass
            self.card_page = None
            self._human_delay(0.5, 1)
            logger.info("Вкладка закрыта, возврат в выдачу")

    async def get_seller_period_months(self) -> int:
        text = await self._get_element_text(self.XPATH_SELLER_PERIOD, timeout=5)
        return self._parse_months_on_wb(text)

    async def get_seller_sales(self) -> Optional[int]:
        text = await self._get_element_text(self.XPATH_SELLER_SALES, timeout=5)
        return self._parse_number(text)

    async def get_seller_reviews(self) -> Optional[int]:
        text = await self._get_element_text(self.XPATH_SELLER_REVIEWS, timeout=5)
        return self._parse_number(text)

    async def check_seller_filters(
        self,
        seller_period: Optional[int] = None,
        seller_count_sales: Optional[Union[int, Tuple[int, int]]] = None,
        seller_count_reviews: Optional[int] = None
    ) -> Tuple[bool, str]:
        if seller_period is not None:
            months = await self.get_seller_period_months()
            if months < seller_period:
                return False, f"На WB {months} мес < {seller_period}"

        if seller_count_sales is not None:
            sales = await self.get_seller_sales()
            if sales is None:
                return False, "Продажи не найдены"
            if isinstance(seller_count_sales, tuple):
                min_sales, max_sales = seller_count_sales
                if sales < min_sales:
                    return False, f"Продаж {sales} < {min_sales}"
                if max_sales is not None and sales > max_sales:
                    return False, f"Продаж {sales} > {max_sales}"
            else:
                if sales < seller_count_sales:
                    return False, f"Продаж {sales} < {seller_count_sales}"

        if seller_count_reviews is not None:
            reviews = await self.get_seller_reviews()
            if reviews is None:
                return False, "Отзывы продавца не найдены"
            if reviews < seller_count_reviews:
                return False, f"Отзывов {reviews} < {seller_count_reviews}"

        return True, "OK"

    # =====================================================
    # Извлечение данных ИП
    # =====================================================

    async def get_seller_info(self, scu: str) -> Optional[Dict]:
        """
        Получить ИНН, ОГРНИП, имя продавца.
        Кликает на 'i', парсит тултип.
        """
        info_btn = await self._wait_for_element(self.XPATH_SELLER_INFO_BTN, timeout=5)
        if not info_btn:
            logger.warning("Кнопка информации о продавце не найдена")
            return None

        await self._click_element(info_btn)
        self._human_delay(1, 2)

        line1 = await self._get_element_text(self.XPATH_SELLER_INFO_LINE1, timeout=5)
        if not line1:
            logger.warning("Информация о продавце не найдена")
            return None

        last_name = ''
        name = ''
        surname = ''
        line1_lower = line1.lower()

        is_ip = ('индивидуальный предприниматель' in line1_lower or
                 re.search(r'\bип\b', line1_lower))

        if not is_ip:
            self._log(f"  ⏭ не ИП: {line1[:60]}")
            return None

        # Убираем "ИП" / "индивидуальный предприниматель" где бы они ни стояли
        fio_clean = re.sub(
            r'(?:индивидуальный\s+предприниматель|(?<!\w)ип(?!\w))',
            '', line1, flags=re.IGNORECASE
        ).strip()
        fio_words = fio_clean.split()
        if len(fio_words) >= 3:
            last_name = _titlecase_name(fio_words[0])
            name = _titlecase_name(fio_words[1])
            surname = _titlecase_name(fio_words[2])
        else:
            self._log(f"  ⚠ ФИО не распарсилось: {line1[:60]}")
            return None

        inn_text = await self._get_element_text(self.XPATH_SELLER_INN, timeout=3)
        inn = None
        if inn_text:
            inn_match = re.search(r'ИНН:\s*(\d+)', inn_text)
            if inn_match:
                inn = inn_match.group(1)

        if not inn:
            self._log("  ⚠ ИНН не найден")
            return None

        ogrnip = ''
        ogrnip_text = await self._get_element_text(self.XPATH_SELLER_OGRNIP, timeout=3)
        if ogrnip_text:
            ogrnip_match = re.search(r'ОГРНИП:\s*(\d+)', ogrnip_text)
            if ogrnip_match:
                ogrnip = ogrnip_match.group(1)

        if not ogrnip:
            ogrn_text = await self._get_element_text(self.XPATH_SELLER_OGRN, timeout=3)
            if ogrn_text:
                ogrn_match = re.search(r'ОГРН:\s*(\d+)', ogrn_text)
                if ogrn_match:
                    ogrnip = ogrn_match.group(1)

        shop = await self._get_element_text(self.XPATH_SELLER_TITLE, timeout=3)
        if not shop:
            logger.warning("Название магазина не найдено")
            return None

        return {
            'inn': inn,
            'last_name': last_name,
            'name': name,
            'surname': surname,
            'patronymic': surname,
            'scu': scu,
            'shop': shop,
            'ogrnip': ogrnip,
            'telegram': ''
        }

    # =====================================================
    # Основной цикл collect()
    # =====================================================

    async def collect(self, query_configs: List[Dict]) -> Dict:
        """
        Основной метод сбора продавцов.

        Args:
            query_configs: [{query, kolvo_for_query, stroke_position, proverka}, ...]
        """
        delay_range = (0, 0)

        self._log("\n" + "="*60)
        self._log("СБОР ПРОДАВЦОВ WILDBERRIES")
        self._log("="*60)
        self._log(f"Запросов: {len(query_configs)}, задача: {self.task_id}")
        self._log("="*60)

        await self.open_wildberries()

        total_saved = 0

        query_index = self.progress.get('current_query_index', 0)
        _resume_query_saved = self.progress.get('current_query_saved', 0)
        _resume_card_index = self.progress.get('current_card_index', 0)
        _is_resuming = _resume_card_index > 0 or _resume_query_saved > 0

        while query_index < len(query_configs):
            if self.stop_requested:
                self._log("Сбор остановлен пользователем")
                break

            config = query_configs[query_index]
            query = config['query']
            kolvo_for_query = config.get('kolvo_for_query', 20)
            stroke_position = config.get('stroke_position', 1)
            proverka = config.get('proverka', None)

            self.clear_skipped()

            if _is_resuming:
                query_saved = _resume_query_saved
                card_index = _resume_card_index
                _is_resuming = False
                self._log(f"↩ Возобновление с карточки {card_index} (уже сохранено: {query_saved})")
            else:
                query_saved = 0
                card_index = (stroke_position - 1) * 5

            self._log(f"\n{'─'*40}")
            self._log(f"Запрос {query_index + 1}/{len(query_configs)}: {query}")
            self._log(f"Лимит: {kolvo_for_query}, с карточки: {card_index}")
            if proverka:
                self._log(f"Лимит проверок: {proverka}")
            self._log(f"{'─'*40}")

            await self.search_query(query)

            start_row = (card_index // self.CARDS_PER_ROW) + 1
            if start_row > 1:
                self._log(f"Прогрузка карточек до строки {start_row}...")
                await self.scroll_to_row(start_row)

            query_checked = 0
            while query_saved < kolvo_for_query:
                if self.stop_requested:
                    self._log("Сбор остановлен пользователем")
                    break

                if proverka and query_checked >= proverka:
                    self._log(f"Достигнут лимит проверок ({proverka})")
                    break

                self.stats['checked'] += 1
                query_checked += 1

                if not await self.click_card(card_index):
                    self._log("Карточки закончились в выдаче")
                    break

                try:
                    scu = self._extract_scu_from_url()

                    if self.stop_requested:
                        await self.close_card_tab()
                        break

                    shop_name = await self.get_seller_name_on_card()
                    self._log(f"[{card_index}] {shop_name or '—'}")
                    if shop_name and self.check_duplicate(shop_name):
                        self._log("  ⏭ дубликат, пропуск")
                        self.stats['skipped_duplicate'] += 1
                        await self.close_card_tab()
                        self._human_delay(*delay_range)
                        card_index += 1
                        continue

                    if self.stop_requested:
                        await self.close_card_tab()
                        break

                    if await self.has_video_as_first_slide():
                        self._log("  ⏭ видео на 1-м слайде, пропуск")
                        self.stats['skipped_card_filter'] += 1
                        self.add_to_skipped(shop_name)
                        await self.close_card_tab()
                        self._human_delay(*delay_range)
                        card_index += 1
                        continue

                    if self.stop_requested:
                        await self.close_card_tab()
                        break

                    if not await self.click_seller_link():
                        self.stats['skipped_seller_filter'] += 1
                        await self.close_card_tab()
                        self._human_delay(*delay_range)
                        card_index += 1
                        continue

                    if self.stop_requested:
                        await self.close_card_tab()
                        break

                    seller_url = self.page.url

                    seller_info = await self.get_seller_info(scu)

                    if seller_info is None:
                        self.stats['skipped_not_ip'] += 1
                        self.add_to_skipped(shop_name)
                        await self.close_card_tab()
                        self._human_delay(*delay_range)
                        card_index += 1
                        continue

                    ogrnip = seller_info.get('ogrnip')
                    if self.check_ogrnip_in_ozon(ogrnip):
                        self._log(f"⚠️ Дубликат ОГРНИП в Ozon")
                        self.stats['skipped_duplicate'] += 1
                        await self.close_card_tab()
                        self._human_delay(*delay_range)
                        card_index += 1
                        continue

                    seller_info['query'] = query
                    seller_info['task_id'] = self.task_id
                    seller_info['seller_url'] = seller_url
                    seller_info['product_url'] = (
                        f"https://www.wildberries.ru/catalog/{scu}/detail.aspx" if scu else ""
                    )
                    seller_info['date'] = datetime.now().strftime('%Y-%m-%d')
                    seller_info['phone'] = ''
                    seller_info['channel'] = ''
                    seller_info['status'] = 'В ожидании'
                    seller_info['account'] = ''

                    await self._save_seller(seller_info)
                    self.stats['saved'] += 1
                    query_saved += 1
                    total_saved += 1

                    self._log(f"✅ Сохранен: {seller_info['shop']} ({query_saved}/{kolvo_for_query})")

                    self.progress['current_query_index'] = query_index
                    self.progress['current_query_saved'] = query_saved
                    self.progress['current_card_index'] = card_index + 1
                    self._save_progress()

                    await self.close_card_tab()

                except Exception as e:
                    logger.error(f"Ошибка обработки карточки {card_index}: {e}")
                    self.stats['errors'] += 1
                    try:
                        await self.close_card_tab()
                    except Exception:
                        pass

                self._human_delay(*delay_range)
                card_index += 1

            if not self.stop_requested:
                query_index += 1
                self.progress['current_query_index'] = query_index
                self.progress['current_query_saved'] = 0
                self.progress['current_card_index'] = 0
                self._save_progress()

                if query_index < len(query_configs):
                    self._log(f"\nЗадержка перед следующим запросом...")
                    self._human_delay(*delay_range)

        if not self.stop_requested and query_index >= len(query_configs):
            self._log("\nВсе запросы обработаны")
            self._reset_progress()

        self._log("\n" + "="*60)
        self._log("ИТОГИ СБОРА")
        self._log("="*60)
        self._log(f"Сохранено: {self.stats['saved']}")
        self._log(f"Проверено: {self.stats['checked']}")
        self._log(f"Дубликатов: {self.stats['skipped_duplicate']}")
        self._log(f"Не прошли фильтр карточки: {self.stats['skipped_card_filter']}")
        self._log(f"Не прошли фильтр продавца: {self.stats['skipped_seller_filter']}")
        self._log(f"Нет данных (ИНН): {self.stats['skipped_not_ip']}")
        self._log(f"Ошибок: {self.stats['errors']}")
        self._log("="*60)

        return {
            'total_saved': self.stats['saved'],
            'total_checked': self.stats['checked'],
            **self.stats
        }

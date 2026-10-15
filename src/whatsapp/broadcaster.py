"""
WhatsApp Web — DOM-автоматизация для рассылки.

Работает поверх ``page`` от ``WhatsAppClientManager`` (окно уже открыто и
залогинено — менеджер тут не трогаем, только кликаем по DOM).

Поток ``send_to_number``:
    Новый чат → Новый контакт → имя/фамилия/телефон → проверка регистрации
    (есть ли номер в WhatsApp) → Сохранить контакт → (медиа через файловый
    диалог) + текст → Отправить.

ВАЖНО: селекторы привязаны к текущему DOM WhatsApp Web (сняты с реального
интерфейса). При обновлениях WhatsApp их, возможно, придётся подправить — все
вынесены в константы ниже. Предпочтение data-testid / data-icon / id / aria-label,
а не сгенерированным классам (x1c4vz4f и т.п.).
"""

import asyncio
from typing import Optional

from playwright.async_api import Page

from src.core.logger import get_logger

logger = get_logger(__name__)

# ── Локаторы (подтверждены по реальному DOM) ──
SEL_NEW_CHAT = ['button:has([data-icon="new-chat-outline"])', 'button[aria-label="Новый чат"]']
NEW_CONTACT_TEXT = "Новый контакт"
NEW_CONTACT_XY = (122, 205)            # запасной клик по координате, если по тексту не нашли
SEL_FIRST_NAME = 'div[contenteditable="true"][aria-label="Имя"]'
SEL_LAST_NAME = 'div[contenteditable="true"][aria-label="Фамилия"]'
SEL_PHONE = 'input[data-testid="phone-number-input"], input[aria-label="Номер телефона"]'
SEL_REG_STATUS = '#contact-phone-number-fields-error'   # текст «(не) зарегистрирован в WhatsApp»
SEL_SAVE = '[data-testid="save-contact-btn"], [aria-label="Сохранить контакт"]'
SEL_ATTACH = ['button:has([data-icon="plus-rounded"])', 'button[aria-label="Прикрепить"]']
SEL_PHOTO_VIDEO = 'button[aria-label="Фото и видео"]'
SEL_COMPOSE = '[data-testid="conversation-compose-box-input"]'
SEL_CAPTION = '[data-testid="media-caption-input-container"]'
SEL_SEND = 'div[role="button"]:has([data-icon="wds-ic-send-filled"])'


class WhatsAppBroadcaster:
    """Автоматизация отправки одного сообщения на один номер через WhatsApp Web."""

    def __init__(self, page: Page):
        self.page = page

    async def _click_first(self, selectors, timeout: int = 8000):
        """Кликнуть первый видимый из списка селекторов (str или list)."""
        if isinstance(selectors, str):
            selectors = [selectors]
        last_err = None
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                await loc.wait_for(state="visible", timeout=timeout)
                await loc.click()
                return True
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        return False

    async def _type_into(self, selector: str, text: str, multiline: bool = False, timeout: int = 8000):
        """Очистить поле и ввести текст живыми нажатиями (надёжно для lexical-полей).

        В многострочном режиме перевод строки делается Shift+Enter, чтобы не
        отправить сообщение раньше времени (Enter в поле чата = отправка).
        """
        loc = self.page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.click()
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Delete")
        if not text:
            return
        if multiline:
            for i, line in enumerate(text.split("\n")):
                if i:
                    await self.page.keyboard.down("Shift")
                    await self.page.keyboard.press("Enter")
                    await self.page.keyboard.up("Shift")
                if line:
                    await self.page.keyboard.type(line)
        else:
            await self.page.keyboard.type(text)

    async def _wait_registration(self, timeout: int = 9000) -> Optional[bool]:
        """True — номер в WhatsApp, False — нет, None — не удалось определить."""
        try:
            await self.page.wait_for_function(
                """() => {
                    const el = document.getElementById('contact-phone-number-fields-error');
                    return el && /зарегистрир/i.test(el.innerText);
                }""",
                timeout=timeout,
            )
        except Exception:
            return None
        try:
            txt = (await self.page.locator(SEL_REG_STATUS).first.inner_text()).lower()
        except Exception:
            return None
        if "не зарегистрир" in txt:
            return False
        if "зарегистрир" in txt:
            return True
        return None

    async def _open_contact_form(self, first: str, last_field: str, digits: str) -> Optional[bool]:
        """Открыть форму нового контакта, заполнить и вернуть статус регистрации.

        True — номер в WhatsApp, False — нет, None — не определилось.
        """
        page = self.page
        # Сбросить возможные открытые попапы/панели
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

        # Новый чат
        await self._click_first(SEL_NEW_CHAT)
        await asyncio.sleep(2)   # панель «Новый контакт» появляется не сразу

        # Новый контакт (по тексту, иначе по координате)
        try:
            await page.get_by_text(NEW_CONTACT_TEXT, exact=False).first.click(timeout=4000)
        except Exception:
            await page.mouse.click(*NEW_CONTACT_XY)

        # Форма: имя / фамилия / телефон (страна по умолчанию RU +7)
        await self._type_into(SEL_FIRST_NAME, first or "")
        await self._type_into(SEL_LAST_NAME, last_field or "")
        phone = page.locator(SEL_PHONE).first
        await phone.wait_for(state="visible", timeout=8000)
        await phone.click()
        await phone.fill(digits)

        return await self._wait_registration()

    async def _close_form(self):
        """Закрыть форму нового контакта без сохранения."""
        for _ in range(2):
            try:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def check_number(self, first: str, last: str, digits: str, nth: int) -> Optional[bool]:
        """Только проверить регистрацию номера в WhatsApp (контакт НЕ сохраняется).

        True — номер в WhatsApp, False — нет, None — не определилось.
        """
        last_field = last if nth == 0 else f"{last} {digits}".strip()
        registered = await self._open_contact_form(first, last_field, digits)
        await self._close_form()
        return registered

    async def send_to_number(self, first: str, last: str, digits: str,
                             text: str, media_path: str, nth: int) -> dict:
        """Создать контакт и отправить ему сообщение.

        Возвращает {"registered": True|False|None, "sent": bool}.
        Для 2-го+ номера продавца фамилия = "Фамилия <номер>" (чтобы различать
        контакты одного человека с разными номерами).
        """
        page = self.page
        last_field = last if nth == 0 else f"{last} {digits}".strip()

        registered = await self._open_contact_form(first, last_field, digits)
        if registered is not True:
            await self._close_form()
            return {"registered": registered, "sent": False}

        # Сохранить контакт → должен открыться чат
        await self._click_first(SEL_SAVE)
        await page.locator(SEL_COMPOSE).first.wait_for(state="visible", timeout=15000)

        # Отправка
        if media_path:
            async with page.expect_file_chooser() as fc_info:
                await self._click_first(SEL_ATTACH)
                await page.locator(SEL_PHOTO_VIDEO).first.click(timeout=8000)
            chooser = await fc_info.value
            await chooser.set_files(media_path)
            await page.locator(SEL_CAPTION).first.wait_for(state="visible", timeout=20000)
            if text:
                await self._type_into(SEL_CAPTION, text, multiline=True)
            await self._click_first(SEL_SEND)
        else:
            await self._type_into(SEL_COMPOSE, text or "", multiline=True)
            await page.keyboard.press("Enter")

        await asyncio.sleep(1.5)   # дать сообщению уйти
        return {"registered": True, "sent": True}

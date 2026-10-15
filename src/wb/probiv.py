"""
Пробив селлеров WB через Telegram-бота
"""

import re
from typing import Dict, List, Optional

BOT_USERNAME = 'Sudhebdbh_bot'

_FIELD_PATTERNS = {
    'birthdate': re.compile(r'ДАТА РОЖДЕНИЯ:\s*(.+)'),
    'phone': re.compile(r'ТЕЛЕФОН:\s*(.+)'),
    'email': re.compile(r'ПОЧТА:\s*(.+)'),
}


def _extract_field(text: str, key: str) -> Optional[str]:
    m = _FIELD_PATTERNS[key].search(text)
    return m.group(1).strip() if m else None


def _extract_phones(phone_field: str) -> List[str]:
    phones = []
    for part in re.split(r'[,\n]+', phone_field):
        digits = re.sub(r'\D', '', part)
        if re.fullmatch(r'7\d{10}', digits) and digits not in phones:
            phones.append(digits)
    return phones


def evaluate_response(text: str) -> Dict:
    """Разобрать ответ бота по правилам пользователя.

    Возвращает один из вариантов:
      {'status': 'phone_found', 'phones': [...], 'email': ..., 'birthdate': ...}
      {'status': 'no_phone',    'email': ..., 'birthdate': ...}
      {'status': 'not_found',   'email': ..., 'birthdate': None}
    """
    birthdate_raw = _extract_field(text, 'birthdate')
    phone_field = _extract_field(text, 'phone')
    email_field = _extract_field(text, 'email')

    email = None
    if email_field and ',' not in email_field:
        email = email_field.strip()

    # Дата рождения: одна без запятой → используем, с запятой → несколько человек
    multiple_birthdates = bool(birthdate_raw and ',' in birthdate_raw)
    birthdate = birthdate_raw.strip() if birthdate_raw and not multiple_birthdates else None

    if phone_field:
        phones = _extract_phones(phone_field)
        if phones:
            return {'status': 'phone_found', 'phones': phones, 'email': email, 'birthdate': birthdate}

    if multiple_birthdates:
        return {'status': 'not_found', 'email': email, 'birthdate': None}

    return {'status': 'no_phone', 'email': email, 'birthdate': birthdate}


async def probiv_value(client, bot_username: str, value: str, timeout: int = 60, log_callback=None) -> Dict:
    """Отправить значение (ИНН или почту) боту и получить разобранный ответ.

    Бот шлёт до 3 сообщений: заглушку "Ваш запрос обрабатывается", затем
    текст с данными ("Найденные данные: ..."), затем файл с подписью.
    Дочитываем все 3, чтобы файл не "утёк" в следующий обмен и не был
    принят за ответ на следующий запрос.
    """
    bot = await client.get_entity(bot_username)
    async with client.conversation(bot, timeout=timeout) as conv:
        await conv.send_message(value)

        text = ''
        for i in range(3):
            try:
                resp = await conv.get_response(timeout=timeout if i < 2 else 15)
            except Exception as e:
                if log_callback:
                    log_callback(f"  [debug] get_response#{i} ошибка: {e}")
                break
            raw = (resp.raw_text or '').strip()
            if log_callback:
                preview = raw[:60].replace('\n', ' ')
                log_callback(f"  [debug] get_response#{i}: raw_text={preview!r}")
            if 'технические работы' in raw.lower() or 'лимит запросов превышен' in raw.lower():
                return {'status': 'bot_down'}
            if raw.startswith('Найденные данные') and not text:
                text = raw
            elif raw == 'Не найдено':
                # На "Не найдено" бот файл не присылает - не ждём его
                break

        result = evaluate_response(text)
        if log_callback:
            log_callback(f"  [debug] evaluate_response → {result}")
        return result


async def probiv_seller(
    client,
    inn: str,
    bot_username: str = BOT_USERNAME,
    timeout: int = 60,
    name_query: Optional[str] = None,
    log_callback=None,
) -> Dict:
    """Полный цикл пробива одного продавца по ИНН.

    Алгоритм:
    1. Запрос по ИНН
    2. Если телефон найден → готово
    3. Если несколько людей (not_found) → пробуем email
    4. Если один человек без телефона:
       - Пробуем "Фамилия Имя Отчество [дата]" (приоритет выше почты)
       - Если не нашли и есть email → пробуем email

    Возвращает {'found': True, 'phones': [...]} или {'found': False}
    """
    result = await probiv_value(client, bot_username, inn, timeout=timeout, log_callback=log_callback)

    if result['status'] == 'bot_down':
        return {'found': False, 'bot_down': True}
    if result['status'] == 'phone_found':
        return {'found': True, 'phones': result['phones']}

    email = result.get('email')
    birthdate = result.get('birthdate')

    if result['status'] == 'not_found':
        # Несколько дат рождения — имя+дата не поможет, пробуем email
        if email:
            if log_callback:
                log_callback(f"  [debug] несколько записей, пробуем почту: {email}")
            r = await probiv_value(client, bot_username, email, timeout=timeout, log_callback=log_callback)
            if r['status'] == 'bot_down':
                return {'found': False, 'bot_down': True}
            if r['status'] == 'phone_found':
                return {'found': True, 'phones': r['phones']}
        return {'found': False}

    # status == 'no_phone'
    if name_query:
        fio_query = f"{name_query} {birthdate}" if birthdate else name_query
        if log_callback:
            log_callback(f"  [debug] пробуем ФИО+дата: {fio_query}")
        r2 = await probiv_value(client, bot_username, fio_query, timeout=timeout, log_callback=log_callback)
        if r2['status'] == 'bot_down':
            return {'found': False, 'bot_down': True}
        if r2['status'] == 'phone_found':
            return {'found': True, 'phones': r2['phones']}
        if r2.get('email'):
            email = r2['email']

    if email:
        if log_callback:
            log_callback(f"  [debug] не нашли телефон, пробуем почту: {email}")
        r3 = await probiv_value(client, bot_username, email, timeout=timeout, log_callback=log_callback)
        if r3['status'] == 'bot_down':
            return {'found': False, 'bot_down': True}
        if r3['status'] == 'phone_found':
            return {'found': True, 'phones': r3['phones']}

    return {'found': False}

"""
Дозаполнение last_name / patronymic для продавцов, у которых фамилия не сохранилась.

Проходит по seller_url каждого продавца без last_name,
открывает страницу в браузере, кликает кнопку «i» и парсит ФИО заново.
Сохраняет результат после каждого продавца.

Запуск:
    python tasks/wb/backfill_names.py
"""

import asyncio
import json
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from playwright.async_api import async_playwright

from src.wb.client import WB_PROFILE_DIR
from src.core.config import Config

SELLERS_FILE = Config.DATA_DIR / 'wb' / 'sellers.json'

XPATH_INFO_BTN   = "//*[@id='reactContainers']/div[1]/div/div[2]/div/div/div[1]/div[2]/div[1]/span"
XPATH_LINE1      = "//div[contains(@class, 'tooltip-supplier')]//div[@class='tooltip__content']/p[1]"
XPATH_INN        = "//div[contains(@class, 'tooltip-supplier')]//p[contains(., 'ИНН:')]"
XPATH_OGRNIP     = "//div[contains(@class, 'tooltip-supplier')]//p[contains(., 'ОГРНИП:')]"
XPATH_OGRN       = "//div[contains(@class, 'tooltip-supplier')]//p[contains(., 'ОГРН:') and not(contains(., 'ОГРНИП:'))]"


def _parse_fio(line1: str):
    """Убирает ИП/ИП с любого конца, возвращает (last_name, name, patronymic) или (None, None, None)."""
    clean = re.sub(
        r'(?:индивидуальный\s+предприниматель|(?<!\w)ип(?!\w))',
        '', line1, flags=re.IGNORECASE
    ).strip()
    words = clean.split()
    if len(words) >= 3:
        return words[0], words[1], words[2]
    return None, None, None


def _load():
    with open(SELLERS_FILE, encoding='utf-8') as f:
        return json.load(f)


def _save(sellers):
    with open(SELLERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(sellers, f, ensure_ascii=False, indent=2)


async def main():
    sellers = _load()
    to_update = [s for s in sellers if not s.get('last_name') and s.get('seller_url')]
    print(f"Продавцов без фамилии: {len(to_update)}  (всего в базе: {len(sellers)})")

    if not to_update:
        print("Нечего обновлять.")
        return

    WB_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(WB_PROFILE_DIR),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-notifications",
                "--lang=ru-RU",
                "--start-maximized",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        page = context.pages[0] if context.pages else await context.new_page()

        updated = 0
        failed  = 0

        for i, seller in enumerate(to_update, 1):
            inn        = seller.get('inn', '?')
            seller_url = seller['seller_url']
            print(f"\n[{i}/{len(to_update)}] ИНН {inn}")
            print(f"  → {seller_url}")

            try:
                await page.goto(seller_url, timeout=30_000)
                await page.wait_for_load_state('domcontentloaded', timeout=15_000)
                await asyncio.sleep(2)

                info_btn = page.locator(XPATH_INFO_BTN).first
                try:
                    await info_btn.wait_for(state='visible', timeout=8_000)
                except Exception:
                    print("  ⚠ кнопка «i» не найдена — пропуск")
                    failed += 1
                    continue

                await info_btn.click()
                await asyncio.sleep(1)

                line1_el = page.locator(XPATH_LINE1).first
                try:
                    await line1_el.wait_for(state='visible', timeout=5_000)
                except Exception:
                    print("  ⚠ тултип не открылся — пропуск")
                    failed += 1
                    continue

                line1 = (await line1_el.text_content() or '').strip()
                last_name, name, patronymic = _parse_fio(line1)

                if not last_name:
                    print(f"  ⚠ не распарсилось: {line1[:70]}")
                    failed += 1
                    continue

                print(f"  ✅ {last_name} {name} {patronymic}")

                for s in sellers:
                    if s.get('inn') == inn:
                        s['last_name']  = last_name
                        s['name']       = name
                        s['surname']    = patronymic
                        s['patronymic'] = patronymic
                        break

                _save(sellers)
                updated += 1

            except Exception as e:
                print(f"  ❌ ошибка: {e}")
                failed += 1

            await asyncio.sleep(1.5)

        await context.close()

    print(f"\n{'='*40}")
    print(f"Готово. Обновлено: {updated}  |  Не удалось: {failed}  |  Всего было: {len(to_update)}")


if __name__ == '__main__':
    asyncio.run(main())

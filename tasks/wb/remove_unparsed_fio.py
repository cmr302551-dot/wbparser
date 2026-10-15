"""
Удаление продавцов с нераспарсенным ФИО (меньше 3 слов в имени, например китайцы).

Чистит data/wb/sellers.json от записей, где last_name пустой —
такие продавцы больше не должны были попадать в базу.

Запуск:
    python tasks/wb/remove_unparsed_fio.py
"""

import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.core.config import Config

SELLERS_FILE = Config.DATA_DIR / 'wb' / 'sellers.json'


def main():
    sellers = json.loads(SELLERS_FILE.read_text(encoding='utf-8'))

    kept = []
    removed = []
    for seller in sellers:
        if (seller.get('last_name') or '').strip():
            kept.append(seller)
        else:
            removed.append(seller)

    if not removed:
        print("Нечего удалять — все продавцы с распарсенным ФИО.")
        return

    SELLERS_FILE.write_text(
        json.dumps(kept, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    print(f"Удалено продавцов: {len(removed)}")
    for s in removed:
        print(f"  ИНН {s.get('inn')} — магазин {s.get('shop')!r}")
    print(f"Осталось продавцов: {len(kept)}")


if __name__ == '__main__':
    main()

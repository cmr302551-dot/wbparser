import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import automation_wildberries as automation

async def run():
#    await automation.connect(
#        profile=r"C:\Users\1\AppData\Local\Google\Chrome\User Data\Profile 5",
#        proxy_address="0.0.0.0",
#        proxy_port=0,
#        proxy_username="proxy_user",
#        proxy_password="proxy_pass",
#        proxy_type="socks5"
#    )

    await automation.connect(
        profile=r"C:\Users\1\AppData\Local\Google\Chrome\User Data\Profile 5"
    )


    await automation.collect_sellers(
        search_queries=["стакан"],  # поисковые запросы
        kolvo_for_query=10,  # кол-во продавцов на 1 запрос
        kolvo=10,  # общий лимит на запуск
        delay_range=(2, 5),  # диапазон задержки между карточками (сек)
        stroke_position=30,  # с какой строки выдачи начинать (1 = первая)
        # Фильтры карточки:
        price=300,  # мин. цена товара
        sales_30=10000,  # мин. выручка за 30 дней
        reserve=2,  # мин. дней хватит товара
        days_in_stock=2,  # мин. дней в наличии (макс. 30)
        opening_date=2,
        count_reviews=40,  # мин. отзывов карточки
        rating=4,  # мин. рейтинг (значение: 4 / 4.5 / 5)
        # Фильтры продавца:
        seller_period=12,  # мин. месяцев на WB
        seller_count_sales=5000,  # мин. продаж продавца
        seller_count_reviews=1000  # мин. отзывов продавца
    )

    await automation.disconnect()

asyncio.run(run())
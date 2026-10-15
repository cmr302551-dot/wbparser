import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import automation_wildberries as automation

async def run():

    await automation.connect(
        profile=r"C:\Users\1\AppData\Local\Google\Chrome\User Data\Profile 5"
    )


#    await automation.collect_sellers(
#        search_queries=["Косметичка"],  # поисковые запросы
#        kolvo_for_query=6,  # кол-во продавцов на 1 запрос
#        kolvo=6,  # общий лимит на запуск
#        delay_range=(2, 4),  # диапазон задержки между карточками (сек)
#        stroke_position=51,  # с какой строки выдачи начинать (1 = первая)
#        # Фильтры карточки:
#        price=100,  # мин. цена товара
#        sales_30=(10000, 200000),  # мин. выручка за 30 дней
#        reserve=5,  # мин. дней хватит товара
#        days_in_stock=1,  # мин. дней в наличии (макс. 30)
#        opening_date=1,
#        count_reviews=5,  # мин. отзывов карточки
#        rating=4,  # мин. рейтинг (значение: 4 / 4.5 / 5)
#        # Фильтры продавца:
#        seller_period=6,  # мин. месяцев на WB
#        seller_count_sales=(100, 1000000),  # мин. продаж продавца
#        seller_count_reviews=100,  # мин. отзывов продавца
#        proverka=400
#    )

#    await automation.collect_sellers(
#        search_queries=["сумка женская"],  # поисковые запросы
#        kolvo_for_query=41,  # кол-во продавцов на 1 запрос
#        kolvo=41,  # общий лимит на запуск
#        delay_range=(2, 4),  # диапазон задержки между карточками (сек)
#        stroke_position=27,  # с какой строки выдачи начинать (1 = первая)
#        # Фильтры карточки:
#        price=1000,  # мин. цена товара
#        sales_30=(10000, 600000),  # мин. выручка за 30 дней
#        reserve=25,  # мин. дней хватит товара
#        days_in_stock=1,  # мин. дней в наличии (макс. 30)
#        opening_date=1,
#        count_reviews=5,  # мин. отзывов карточки
#        rating=4,  # мин. рейтинг (значение: 4 / 4.5 / 5)
#        # Фильтры продавца:
#        seller_period=6,  # мин. месяцев на WB
#        seller_count_sales=(100, 1000000),  # мин. продаж продавца
#        seller_count_reviews=100,  # мин. отзывов продавца,
#        proverka=400
#    )

#    await automation.collect_sellers(
#        search_queries=["часы женские"],  # поисковые запросы
#        kolvo_for_query=28,  # кол-во продавцов на 1 запрос
#        kolvo=28,  # общий лимит на запуск
#        delay_range=(2, 4),  # диапазон задержки между карточками (сек)
#        stroke_position=81,  # с какой строки выдачи начинать (1 = первая)
#        # Фильтры карточки:
#        price=1000,  # мин. цена товара
#        sales_30=(10000, 600000),  # мин. выручка за 30 дней
#        reserve=25,  # мин. дней хватит товара
#        days_in_stock=1,  # мин. дней в наличии (макс. 30)
#        opening_date=1,
#        count_reviews=5,  # мин. отзывов карточки
#        rating=4,  # мин. рейтинг (значение: 4 / 4.5 / 5)
#        # Фильтры продавца:
#        seller_period=6,  # мин. месяцев на WB
#        seller_count_sales=(100, 1000000),  # мин. продаж продавца
#        seller_count_reviews=100,  # мин. отзывов продавца
#        proverka=300
#    )

    await automation.collect_sellers(
        search_queries=["массажер для тела"],  # поисковые запросы
        kolvo_for_query=23,  # кол-во продавцов на 1 запрос
        kolvo=23,  # общий лимит на запуск
        delay_range=(2, 4),  # диапазон задержки между карточками (сек)
        stroke_position=67,  # с какой строки выдачи начинать (1 = первая)
        # Фильтры продавца (отключены):
        # seller_period=6,  # мин. месяцев на WB
        # seller_count_sales=(100, 1000000),  # мин. продаж продавца
        # seller_count_reviews=100,  # мин. отзывов продавца
        proverka=400
    )

#    await automation.collect_sellers(
#        search_queries=["фен для волос"],  # поисковые запросы
#        kolvo_for_query=60,  # кол-во продавцов на 1 запрос
#        kolvo=60,  # общий лимит на запуск
#        delay_range=(2, 4),  # диапазон задержки между карточками (сек)
#        stroke_position=14,  # с какой строки выдачи начинать (1 = первая)
#        # Фильтры карточки:
#        price=1000,  # мин. цена товара
#        sales_30=(10000, 600000),  # мин. выручка за 30 дней
#        reserve=15,  # мин. дней хватит товара
#        days_in_stock=1,  # мин. дней в наличии (макс. 30)
#        opening_date=1,
#        count_reviews=5,  # мин. отзывов карточки
#        rating=4,  # мин. рейтинг (значение: 4 / 4.5 / 5)
#        # Фильтры продавца:
#        seller_period=6,  # мин. месяцев на WB
#        seller_count_sales=(100, 1000000),  # мин. продаж продавца
#        seller_count_reviews=100,  # мин. отзывов продавца
#        proverka=400
#    )

#    await automation.collect_sellers(
#        search_queries=["подарочный набор уходовой косметики для женщин"],  # поисковые запросы
#        kolvo_for_query=60,  # кол-во продавцов на 1 запрос
#        kolvo=60,  # общий лимит на запуск
#        delay_range=(2, 4),  # диапазон задержки между карточками (сек)
#        stroke_position=5,  # с какой строки выдачи начинать (1 = первая)
#        # Фильтры карточки:
#        price=500,  # мин. цена товара
#        sales_30=(10000, 600000),  # мин. выручка за 30 дней
#        reserve=15,  # мин. дней хватит товара
#        days_in_stock=1,  # мин. дней в наличии (макс. 30)
#        opening_date=1,
#        count_reviews=5,  # мин. отзывов карточки
#        rating=4,  # мин. рейтинг (значение: 4 / 4.5 / 5)
#        # Фильтры продавца:
#        seller_period=6,  # мин. месяцев на WB
#        seller_count_sales=(100, 1000000),  # мин. продаж продавца
#        seller_count_reviews=100, # мин. отзывов продавца,
#        proverka=400
#    )


    await automation.disconnect()

asyncio.run(run())
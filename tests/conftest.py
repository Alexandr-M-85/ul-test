from datetime import date
from uuid import uuid4
import pandas as pd
import numpy as np
import psycopg
import pytest
from psycopg import sql

from etl.db import SQL_DIR, pg_connect
from etl.pipeline import initialize


@pytest.fixture(scope="function")
def ephemeral_db():
    # Тест на создание изолированной базы
    db_name = "de_test_" + uuid4().hex
    with pg_connect() as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))

        try:
            with psycopg.connect(dbname=db_name, autocommit=True) as connection:
                connection.execute("CREATE SCHEMA raw; SET search_path TO raw, public")
                # Читаем структуру и убираем BOM-маркер кодировки
                schema_sql = (SQL_DIR / "schema.sql").read_text(encoding="utf-8-sig")
                connection.execute(schema_sql)
                # Запускаем функцию инициализации
                initialize(connection)
            # Передаем имя базы (или объект connection) в тело теста
            yield db_name

        finally:
            # Блок гарантированного удаления базы после окончания теста.
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))



@pytest.fixture
def db(test_database):
    from psycopg.rows import dict_row

    with psycopg.connect(dbname=test_database, autocommit=True, row_factory=dict_row) as connection:
        connection.execute(
            "TRUNCATE raw.prices, raw.cities, raw.body_types, raw.exchange_rates, "
            "raw.price_group_stats, core.prices, core.cities, core.dq_quarantine_prices, "
            "mart.market_corridor, mart.ml_price_predictions, mart.ml_model_runs RESTART IDENTITY CASCADE"
        )
        connection.execute(
            "INSERT INTO raw.cities (gc_id,name,full_name_local,full_name_gc,latitude,longitude) VALUES "
            "(1,'Казань','Казань, Татарстан','Казань',55.8,49.1),"
            "(1,'Дубль','Казань, Другой регион','Казань',0,0),"
            "(2,'Москва','Москва, г. Москва','Москва',55.7,37.6)"
        )
        connection.execute(
            'INSERT INTO raw.body_types (id,name,"id_from_1C",std_id) VALUES '
            "(56,'тент',56,56),(57,'тент бок',57,56),(61,'тент полный',61,56),"
            "(1,'бортовая',1,15),(15,'борт',15,15),(4,'реф',4,4)"
        )
        yield connection


@pytest.fixture
def add_price(db):
    def insert(**changes):
        values = {
            "loading_date": date(2026, 10, 10), "created_at": "2026-10-01 12:00:00",
            "loading_city_gcid": 1, "unloading_city_gcid": 2, "distance": 100.0,
            "total_weight": 20.0, "total_volume": 80.0, "body_type": 56,
            "price": 10000.0, "currency_code": "643", "organization_id": 1,
            "price_type": 3,
        }
        values.update(changes)
        statement = sql.SQL("INSERT INTO raw.prices ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values),
        )
        price_id = db.execute(statement, tuple(values.values())).fetchone()["id"]
        db.execute("INSERT INTO raw.price_dates(price_id, price_date) VALUES (%s,%s)",
                   (price_id, values["loading_date"]))
        return price_id
    return insert


@pytest.fixture
def df():
    dates = pd.to_datetime([
        "2026-08-25", "2026-09-02", "2026-09-03", "2026-09-12", "2026-09-24",
        "2026-10-02", "2026-10-06", "2026-10-26", "2026-10-28", "2027-01-01",
    ])
    size = len(dates)
    return pd.DataFrame({
        "price_id": range(1, size + 1), "loading_date": dates,
        "loading_city_gcid": 1, "unloading_city_gcid": 2,
        "origin_region": "г. Москва", "destination_region": "Татарстан",
        "origin_latitude": 55.7, "origin_longitude": 37.6,
        "destination_latitude": 55.8, "destination_longitude": 49.1,
        "distance": np.arange(size) * 100 + 100,
        "total_weight": 20, "total_volume": 80, "weight_category": "FTL",
        "volume_category": "FTL", "body_group": "Тент",
        "price_rub": np.arange(size) * 1000 + 10000,
        "is_fact": True, "price_type": 3, "complex": False, "outlier_mark": False,
    })

from datetime import date
from decimal import Decimal

import pytest

from etl.load import load_core, load_mart

START, END, AS_OF = date(2025, 1, 1), date(2027, 1, 1), date(2026, 10, 26)


@pytest.mark.parametrize("change,reason", [
    ({"price": 0}, "INVALID_PRICE"),
    ({"price": -1}, "INVALID_PRICE"),
    ({"price": 1000}, "RUB_PRICE_TOO_LOW"),
    ({"distance": 0}, "INVALID_DISTANCE"),
    ({"total_weight": -1}, "INVALID_WEIGHT"),
    ({"total_volume": -1}, "INVALID_VOLUME"),
    ({"loading_city_gcid": 9999}, "UNKNOWN_ORIGIN"),
    ({"unloading_city_gcid": 9999}, "UNKNOWN_DESTINATION"),
    ({"currency_code": "840"}, "MISSING_FX_RATE"),
    ({"price": float("nan")}, "INVALID_PRICE"),
    ({"distance": float("inf")}, "INVALID_DISTANCE"),
])
def invalid_rows(db, add_price, change, reason):
    # Проверка обработки ошибочных данных
    price_id = add_price(**change)
    counts = load_core(db, START, END)
    assert counts["loaded"] == 0 and counts["quarantined"] == 1
    rejected = db.execute("SELECT * FROM core.dq_quarantine_prices").fetchone()
    assert rejected["price_id"] == price_id
    assert reason in rejected["reasons"]


def replace_records(db, add_price):
    # Корректно обрабатываем изменения
    first = add_price()
    corrected = add_price(price=0)
    moved = add_price(loading_date=date(2026, 9, 12))
    load_core(db, START, END)
    load_mart(db, AS_OF)
    before = db.execute("SELECT * FROM mart.mart_route_card ORDER BY month").fetchall()
    load_core(db, START, END)
    load_mart(db, AS_OF)
    assert db.execute("SELECT * FROM mart.mart_route_card ORDER BY month").fetchall() == before
    db.execute("UPDATE raw.prices SET price=20000 WHERE id=%s", (corrected,))
    db.execute("DELETE FROM raw.prices WHERE id=%s", (first,))
    db.execute("UPDATE raw.prices SET loading_date='2026-10-20' WHERE id=%s", (moved,))
    load_core(db, date(2026, 10, 1), date(2026, 11, 1))
    assert db.execute("SELECT count(*) AS n FROM core.dq_quarantine_prices").fetchone()["n"] == 0
    rows = db.execute("SELECT price_id,loading_date FROM core.prices ORDER BY price_id").fetchall()
    assert [row["price_id"] for row in rows] == [corrected, moved]
    assert rows[1]["loading_date"] == date(2026, 10, 20)


def missing_exact_rate(db, add_price):
    # Проверка на календарное соответствие при переводе данных из иностранной валюты в рубли
    db.execute("INSERT INTO raw.exchange_rates(num_code,value,setting_date) VALUES ('978',100,'2026-10-01')")
    add_price(currency_code="978", nearest_rate_date=date(2026, 10, 2))
    counts = load_core(db, START, END)
    assert counts["reasons"] == {"MISSING_FX_RATE": 1}
    db.execute("INSERT INTO raw.exchange_rates(num_code,value,setting_date) VALUES ('978',110,'2026-10-02')")
    assert load_core(db, START, END)["loaded"] == 1
    assert db.execute("SELECT price_rub FROM core.prices").fetchone()["price_rub"] == Decimal(1100000)
    assert db.execute("SELECT count(*) AS n FROM core.dq_quarantine_prices").fetchone()["n"] == 0


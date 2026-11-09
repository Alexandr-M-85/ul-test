from datetime import date

import psycopg
import pytest

from etl.load import load_core, load_mart

START, END, AS_OF = date(2025, 1, 1), date(2027, 1, 1), date(2026, 10, 26)


def mom_and_yoy_calendar_bounds(db, add_price):
    # Метрики MoM, YoY должны рассчитываться строго по календарным периодам
    add_price(price=83000, loading_date=date(2025, 10, 1))
    add_price(price=75000, loading_date=date(2026, 8, 1))
    add_price(price=109000, loading_date=date(2026, 10, 1))
    load_core(db, START, END)
    load_mart(db, AS_OF)
    october = db.execute("SELECT * FROM mart.mart_route_card WHERE month='2026-10-01'").fetchone()
    assert october["mom_pct"] is None
    assert october["yoy_pct"] == pytest.approx(100)
    assert october["cv_rate"] is None
    add_price(price=94000, loading_date=date(2026, 9, 1))
    load_core(db, date(2026, 9, 1), date(2026, 10, 1))
    load_mart(db, AS_OF)
    mom = db.execute("SELECT mom_pct FROM mart.market_corridor WHERE month='2026-10-01'").fetchone()["mom_pct"]
    assert mom == pytest.approx(25)


def failed_refresh_rollback(db, add_price):
    # Тест на проверку отказоустойчивости при обновлении витрин
    add_price()
    load_core(db, START, END)
    load_mart(db, AS_OF)
    original = db.execute("SELECT * FROM mart.mart_route_card").fetchall()
    db.execute("ALTER TABLE mart.mart_route_card ADD CONSTRAINT stop_it CHECK (trips_count=1)")
    try:
        add_price()
        load_core(db, START, END)
        with pytest.raises(psycopg.errors.CheckViolation):
            load_mart(db, AS_OF)
        assert db.execute("SELECT * FROM mart.mart_route_card").fetchall() == original
    finally:
        db.execute("ALTER TABLE mart.mart_route_card DROP CONSTRAINT stop_it")

import os
import logging
import signal
import argparse

from datetime import date, timedelta
from threading import Event
from contextlib import contextmanager


from etl.db import pg_connect, execute_file
from etl.load import load_core, load_mart
from ml.train import run_model


# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def initialize(conn):
    with conn.transaction():
        execute_file(conn, "dwh.sql")


@contextmanager
def lock_pipeline(conn):
    # Функция блокировки на уровне БД
    acquired = conn.execute("SELECT pg_try_advisory_lock(1290) AS locked").fetchone()
    if not acquired["locked"]:
        raise RuntimeError("Активен другой пайплайн")
    try:
        yield
    finally:
        conn.execute("SELECT pg_advisory_unlock(1290)")


def run(as_of: date, start_date=None, end_date=None):
    # Функция запуска пайплайна
    with pg_connect() as conn, lock_pipeline(conn):
        initialize(conn)

        dates = conn.execute("""
                SELECT min(loading_date) AS start, max(loading_date) AS last
                FROM raw.prices
             """).fetchone()

        if dates["start"] is None:
            raise ValueError("В источнике нет записей")

        start_date = start_date or dates["start"]
        end_date = end_date or dates["last"] + timedelta(days=1)

        logger.info(f"Запуск пересчета данных в core слое")
        counts = load_core(conn, start_date, end_date)
        logger.info(f"Start date = {start_date}, end_date = {end_date}, stats = {counts}")

        logger.info(f"Запуск пересчета данных в mart слое")
        count = load_mart(conn, as_of)
        logger.info(f"Stats = {count}")

        logger.info(f"Запуск моделей для прогноза")
        summary = run_model(conn, as_of)
        logger.info(f"Model = {summary["model_name"]}, train = {summary["train_count"]}, \
                  test = {summary["test_count"]}, future = {summary["future_count"]}, metrics = {summary["metrics"]}")

    logger.info(f"Пайплайн отработал успешно!")


def main():
    param = argparse.ArgumentParser(description="Задать дату снимка as_of, стартовую/конечную дату загрузки, настроить запуск пайплайна")
    param.add_argument("--as_of", type=date.fromisoformat, default=os.getenv("AS_OF", "2026-10-26"))
    param.add_argument("--start_date", type=date.fromisoformat)
    param.add_argument("--end_date", type=date.fromisoformat)
    param.add_argument("--once", action="store_true", help="Однократный запуск или ежедневный")
    args = param.parse_args()
    interval = 0 if args.once else int(os.getenv("RUN_INTERVAL_SECONDS", "86400"))
    if interval < 0:
        param.error("Параметр RUN_INTERVAL_SECONDS не может быть отрицательным")
    stopped = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    while True:
        run(args.as_of, args.start_date, args.end_date)
        if interval == 0 or stopped.wait(interval):
            return


if __name__ == "__main__":
    main()

import psycopg
from psycopg.rows import dict_row
from pathlib import Path


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def pg_connect():
    # Функция настройки к объекту подключения
    return psycopg.connect(autocommit=True, row_factory=dict_row, connect_timeout=10)


def execute_file(conn, file_name, param=None):
    # Функция запуска SQL-файлов
    with psycopg.ClientCursor(conn) as cursor:
        cursor.execute((SQL_DIR / file_name).read_text(encoding="utf-8-sig"), param)


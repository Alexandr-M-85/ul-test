from datetime import date

from etl.db import execute_file


def load_core(conn, start_date: date, end_date: date) -> dict:

    if start_date >= end_date:
        raise ValueError("Начальная дата позже конечной")

    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        execute_file(conn, "load.sql", {"start_date": start_date, "end_date": end_date})

        counts = conn.execute("""        
            SELECT
                count(*) AS read,
                count(*) FILTER (WHERE cardinality(reasons)=0) AS loaded,
                count(*) FILTER (WHERE cardinality(reasons)>0) AS quarantined
            FROM tmp_prices            
        """).fetchone()
        reasons = conn.execute(
            "SELECT reason, count(*) AS count FROM tmp_prices, unnest(reasons) reason GROUP BY reason"
        ).fetchall()
    return {**counts, "reasons": {row["reason"]: row["count"] for row in reasons}}


def load_mart(connection, as_of: date) -> int:
    with connection.transaction():
        execute_file(connection, "mart_route_card.sql", {"as_of": as_of})
        count = connection.execute("SELECT count(*) AS n FROM mart.mart_route_card").fetchone()["n"]
    return count
